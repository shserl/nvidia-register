#!/usr/bin/env python3
"""
nvidia-register — 注册 build.nvidia.com 账号并创建 AI_PLAYGROUNDS_KEY

完整流程（基于真实页面链路，全部实测确认）：
  创建临时邮箱 → build.nvidia.com 填邮箱 → create-account 页填密码 + 过 hCaptcha
  → 验证码页真实键盘输入 → 跳过通行密钥引导页 → 同意/快完成页
  → 创建组织跳过手机验证 → 调 NGC API 建 key → 记录到 CSV

用法:
  pip install -r requirements.txt
  playwright install chromium

  python main.py --init       # 生成 config.toml 配置文件
  # 编辑 config.toml 填入你的信息
  python main.py              # 交互式询问注册数量
  python main.py -n 5         # 直接注册 5 个账号（不询问）
  python main.py --count 3    # 同上

配置文件: config.toml（见 config.toml.example 或 --init 生成）
Ctrl+C  优雅退出：完成当前正在注册的账号后退出。
"""

import asyncio
import json
import re
import secrets
import signal
import string
import sys
import time

from playwright.async_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from config import AppConfig, describe_config, init_config, load_config
from captcha import build_captcha_solver, reset_captcha_state, start_capturing_sitekey
from email_providers import TempEmailProvider, build_email_provider
from passwords import generate_password
from records import append_account_record

# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def _parse_count(argv: list[str]) -> int | None:
    """从命令行参数解析 -n / --count 的值。"""
    args = argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("-n", "--count") and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                print(f"Error: {args[i]} requires a number, got: {args[i + 1]}")
                sys.exit(1)
        i += 1
    return None

def main_cli() -> None:
    args = sys.argv[1:]

    if args and args[0] == "--init":
        init_config()
        return

    config = load_config()

    count = _parse_count(sys.argv)
    if count is None:
        # 交互式询问
        try:
            raw = input("注册账号数量 (默认 1): ").strip()
            count = int(raw) if raw else 1
        except (ValueError, EOFError):
            count = 1
    if count < 1:
        print("数量必须 >= 1")
        return

    try:
        asyncio.run(run(config, count))
    except KeyboardInterrupt:
        print("\n\nInterrupted. Goodbye!")

# ---------------------------------------------------------------------------
#  注册流程
# ---------------------------------------------------------------------------

# Ctrl+C 优雅退出标志
_shutdown = False

def _handle_sigint():
    global _shutdown
    if _shutdown:
        # 第二次 Ctrl+C → 强制退出
        print("\n\nForce exit!")
        sys.exit(1)
    _shutdown = True
    print("\n\nCtrl+C received. Will exit after current account finishes...")

async def run(config: AppConfig, count: int = 1) -> None:
    email_provider = build_email_provider(config)
    captcha_solver = build_captcha_solver(config.captcha)

    print("=" * 60)
    print("NVIDIA Register + API Key Creator")
    print("-" * 60)
    describe_config(config)
    print(f"  注册数量: {count}")
    print("=" * 60)
    print("  (Ctrl+C 优雅退出：完成当前账号后停止)\n")

    # 注册信号处理（仅主线程；GUI 后台线程里 signal 不可用）
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
    except (NotImplementedError, RuntimeError):
        try:
            signal.signal(signal.SIGINT, lambda *_: _handle_sigint())
        except (ValueError, OSError, RuntimeError):
            # 子线程 / 不支持 signal 的环境：靠 GUI 的停止按钮设 _shutdown
            pass

    success_count = 0
    fail_count = 0

    async with async_playwright() as p:
        for i in range(count):
            if _shutdown:
                break

            print(f"\n{'#' * 60}")
            print(f"# 账号 {i + 1} / {count}")
            print(f"{'#' * 60}")

            api_key = await _register_one(p, config, email_provider, captcha_solver)
            if api_key:
                success_count += 1
            else:
                fail_count += 1

            # 非最后一个账号时，间隔一下避免频率限制
            if i < count - 1 and not _shutdown:
                print("\n  等待 15 秒后注册下一个...")
                await asyncio.sleep(15)

    # 汇总
    print("\n" + "=" * 60)
    print(f"完成! 成功: {success_count}, 失败: {fail_count}, 总计: {success_count + fail_count}")
    print("=" * 60)

async def _register_one(
    p,
    config: AppConfig,
    email_provider: TempEmailProvider,
    captcha_solver,
) -> str | None:
    """单个账号的完整注册流程。返回 api_key 或 None。"""
    password = generate_password(12)
    reset_captcha_state()  # 重置 sitekey 缓存，确保每个账号独立

    # cloudaccounts.nvidia.com 在真正 headless 下常渲染空白（无输入框/无按钮）。
    # 「无头」改为有界面但窗口移到屏外，行为与有头一致，用户几乎看不到窗口。
    launch_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    use_real_headless = False
    if config.browser.headless:
        launch_args.extend([
            "--window-position=-2400,-2400",
            "--window-size=1280,800",
        ])
        print("  browser: off-screen headed (cloudaccounts 不兼容真 headless)")
    launch_kwargs: dict = {
        "headless": use_real_headless,
        "args": launch_args,
    }
    # 优先用本机 Edge/Chrome（走系统网络/代理，国内更稳）
    if config.browser.channel:
        launch_kwargs["channel"] = config.browser.channel
    try:
        browser = await p.chromium.launch(**launch_kwargs)
        if config.browser.channel:
            print(f"  browser: {config.browser.channel} (headless={config.browser.headless})")
    except Exception as exc:
        if config.browser.channel:
            print(f"  channel={config.browser.channel} unavailable ({exc}); fallback to Chromium")
            launch_kwargs.pop("channel", None)
            browser = await p.chromium.launch(**launch_kwargs)
        else:
            raise
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    await context.add_init_script(
        """() => {
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = window.chrome || { runtime: {} };
        }"""
    )
    page = await context.new_page()

    try:
        # 1. 创建临时邮箱（nv + 随机字母 + 时间戳，降低撞名/被识别）
        alphabet = string.ascii_lowercase
        rand_letters = "".join(secrets.choice(alphabet) for _ in range(4))
        inbox_name = f"nv{rand_letters}{str(int(time.time()))[-6:]}"
        try:
            inbox = email_provider.create_inbox(inbox_name)
        except Exception as exc:
            print(f"  Email creation failed: {exc}")
            return None
        print(f"\n[1] Email: {inbox.address}")
        print(f"    Password: {password}")

        # 2. 打开可访问的登录入口（本机 build.nvidia.com 常超时）
        print("[2] Opening sign-in page...")
        if not await _open_entry_page(page):
            print("  Failed to open NVIDIA sign-in entry")
            return None
        await _accept_cookie_banner(page)
        # Starfleet 认证脚本加载后偶发整页刷新，稍等再进表单
        await _wait_for_signin_ready(page)

        # 3. 若在站点首页，点 Login；若已在 signin 页则跳过
        print("[3] Open sign-in modal / form...")
        if not await _ensure_email_form(page):
            print("  Email form not found")
            await _print_clickable_snapshot(page)
            return None
        await _accept_cookie_banner(page)

        # 4. 填邮箱 → Next/Continue（跳转到 login.nvgs.nvidia.com|cn create-account）
        print("[4] Submit email...")
        start_capturing_sitekey(page)
        await _ensure_hcaptcha_hook(page)
        if not await _submit_email_step(page, inbox.address):
            print("  Failed at email step")
            await _print_clickable_snapshot(page)
            return None

        # 5. 注册（填密码 → 过 hCaptcha → 提交 → 验证码）
        ok = await register_account(page, inbox, password, email_provider, captcha_solver, config)
        if not ok:
            print("\nRegistration failed")
            return None

        # 6. 状态机处理注册后跳转，直到 session 有效并建 key
        api_key = await finalize_and_create_key(page, config)

        # 7. 记录到 CSV
        if api_key:
            append_account_record(
                path=config.nvidia.output_csv,
                email=inbox.address,
                password=password,
                api_key=api_key,
            )
            print(f"  Record saved to: {config.nvidia.output_csv}")
            print(f"\n  ✓ {inbox.address} → {api_key[:30]}...")
            return api_key
        else:
            print("\nRegistration succeeded but API Key creation failed")
            return None
    finally:
        await _close_browser(browser, config.browser.close_delay_seconds)

# ---------------------------------------------------------------------------
#  子流程
# ---------------------------------------------------------------------------

# 入口说明：
# - ngc.nvidia.com/signin 会跳到 login.nvgs.nvidia.cn 注册/登录（国内常用）
# - build.nvidia.com 在部分网络不稳定，放最后兜底
ENTRY_URLS = (
    "https://ngc.nvidia.com/signin",
    "https://build.nvidia.com/",
)


async def _open_entry_page(page: Page) -> bool:
    """依次尝试可用入口，优先 NGC signin（国内网络更稳）。"""
    last_err: Exception | None = None
    for url in ENTRY_URLS:
        for attempt in range(1, 3):
            try:
                print(f"  try {url} (attempt {attempt})...")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                print(f"  opened: {page.url[:90]}")
                return True
            except Exception as exc:
                last_err = exc
                print(f"  open failed: {exc}")
                await asyncio.sleep(1)
    if last_err:
        print(f"  all entry URLs failed: {last_err}")
    return False


async def _ensure_email_form(page: Page) -> bool:
    """保证页面上有可见的邮箱输入框（signin 页或点 Login 后的弹窗）。"""
    if await page.locator('input[name="email"]:visible, input[type="email"]:visible').count() > 0:
        await _wait_for_stable_email_input(page, settle_seconds=1.5)
        return True

    # catalog / build 首页：点 Login / Sign In
    if await _open_signin_modal(page):
        return True

    # NGC signin 有时 cookie 挡表单，再接受一次
    await _accept_cookie_banner(page)
    await asyncio.sleep(1)
    if await page.locator('input[name="email"]:visible, input[type="email"]:visible').count() > 0:
        return True
    return False


async def _wait_for_signin_ready(page: Page, settle_seconds: float = 2.5) -> None:
    """等 NGC/Starfleet 初始化完成（避免刚输入就被页面刷新清掉）。"""
    deadline = time.time() + 20
    last_url = page.url
    stable_since = time.time()
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        try:
            url = page.url
            has_email = await page.locator("#email, input[name='email'], input[type='email']").count() > 0
        except Exception:
            continue
        if url != last_url:
            last_url = url
            stable_since = time.time()
            continue
        if has_email and (time.time() - stable_since) >= settle_seconds:
            print("  sign-in form ready")
            return
    print("  sign-in settle timeout, continuing anyway")


async def _accept_cookie_banner(page: Page) -> None:
    """OneTrust / NGC cookie 弹窗与偏好中心会挡住表单，必须清掉。"""
    selectors = (
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('Save and Accept')",
        "button:has-text('Reject Optional')",
        "button:has-text('同意')",
        "button:has-text('Accept')",
    )
    clicked = False
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() == 0:
                continue
            if not await btn.is_visible():
                continue
            await btn.click(timeout=3000)
            print(f"  cookie accepted ({sel})")
            clicked = True
            await asyncio.sleep(0.8)
        except Exception:
            continue

    # 强制移除残留遮罩，避免 Continue 一直点不了/校验不触发
    try:
        await page.evaluate(
            """() => {
                for (const id of [
                    'onetrust-banner-sdk',
                    'onetrust-pc-sdk',
                    'ot-sdk-btn-floating',
                    'onetrust-consent-sdk',
                ]) {
                    const el = document.getElementById(id);
                    if (el) el.remove();
                }
                document.querySelectorAll(
                    '.onetrust-pc-dark-filter, #onetrust-pc-dark-filter, .otFlat'
                ).forEach((el) => el.remove());
                document.documentElement.style.overflow = 'auto';
                document.body.style.overflow = 'auto';
            }"""
        )
        if clicked:
            print("  cookie overlays cleared")
    except Exception:
        pass
    await asyncio.sleep(0.5)


async def _open_signin_modal(page: Page) -> bool:
    """点击 header 的 Login / Sign In，打开邮箱表单。"""
    for name in ("Login", "Sign In / Sign Up", "Sign In", "Sign in", "登录"):
        try:
            login = page.get_by_role("button", name=name).first
            if await login.count() == 0:
                login = page.get_by_text(name, exact=False).first
            if await login.count() == 0:
                continue
            await login.click(timeout=5000, force=True)
            break
        except Exception:
            continue

    # 等待邮箱输入框首次出现
    try:
        await page.locator('input[name="email"], input[type="email"]').first.wait_for(
            state="visible", timeout=8000
        )
    except Exception:
        pass

    await _wait_for_stable_email_input(page)
    return await page.locator('input[name="email"]:visible, input[type="email"]:visible').count() > 0


async def _wait_for_stable_email_input(page: Page, settle_seconds: float = 3.0) -> None:
    """等待 signin 弹窗/表单稳定，直到可见 email 输入框稳定。"""
    deadline = time.time() + 15
    stable_since = None
    while time.time() < deadline:
        try:
            visible = await page.locator(
                'input[name="email"]:visible, input[type="email"]:visible'
            ).count()
        except Exception:
            visible = 0
        if visible >= 1:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= settle_seconds:
                return
        else:
            stable_since = None
        await asyncio.sleep(0.5)


async def _submit_email_step(page: Page, email: str) -> bool:
    """填邮箱并点 Next/Continue，跳转到 create-account / login.nvgs 页。"""
    # 提交前再清一次 cookie，headed 模式下偏好中心经常残留
    await _accept_cookie_banner(page)

    email_input = page.locator("#email:visible, input[name='email']:visible, input[type='email']:visible").first
    try:
        await email_input.wait_for(state="visible", timeout=15000)
    except Exception:
        return False

    # 用真实键盘输入，确保 React/Starfleet 受控组件收到 onChange
    for attempt in range(1, 4):
        await email_input.click(force=True)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(email, delay=35)
        await email_input.press("Tab")
        await asyncio.sleep(0.8)
        try:
            actual = await email_input.input_value()
        except Exception:
            actual = ""
        print(f"  email field (try {attempt}): {actual}")
        if actual.strip().lower() == email.strip().lower():
            break
        # 页面刷新会清输入，重等再填
        await _wait_for_signin_ready(page, settle_seconds=1.5)
        await _accept_cookie_banner(page)
        email_input = page.locator(
            "#email:visible, input[name='email']:visible, input[type='email']:visible"
        ).first
    else:
        print("  failed to fill email field")
        return False

    actual = await email_input.input_value()
    if actual.strip().lower() != email.strip().lower():
        print("  failed to fill email field")
        return False

    # 优先点真正的 submit 按钮（避免 role name 匹配到 "Continue Continue"）
    candidates = [
        page.locator('button[type="submit"]:visible').first,
        page.get_by_test_id("kui-button").filter(visible=True).first,
        page.get_by_role("button", name="Continue").filter(visible=True).first,
        page.get_by_role("button", name="Next").filter(visible=True).first,
        page.get_by_role("button", name="继续").filter(visible=True).first,
    ]

    clicked = False
    for next_btn in candidates:
        try:
            if await next_btn.count() == 0:
                continue
            for i in range(50):
                enabled = False
                try:
                    enabled = await next_btn.is_enabled()
                except Exception:
                    enabled = False
                if enabled:
                    await asyncio.sleep(0.5)
                    if not await next_btn.is_enabled():
                        continue
                    try:
                        async with page.expect_navigation(
                            wait_until="domcontentloaded",
                            timeout=45000,
                        ):
                            await next_btn.click()
                    except Exception:
                        if "login.nvgs.nvidia." not in page.url:
                            await next_btn.click(force=True)
                    print(f"  Continue/Next clicked ({i})")
                    clicked = True
                    break
                if i in (8, 20, 35):
                    print(f"  waiting for Continue to enable... ({i})")
                    # 中途再清一次遮罩
                    await _accept_cookie_banner(page)
                await asyncio.sleep(0.4)
            if clicked:
                break
        except Exception:
            continue

    if not clicked:
        # 诊断：DOM 有值但按钮仍禁用，通常是 cookie 或 React 状态未同步
        diag = await page.evaluate(
            """() => {
                const el = document.querySelector('#email, input[name=email], input[type=email]');
                const btn = document.querySelector('button[type=submit]');
                return {
                    value: el && el.value,
                    valid: el && el.validity && el.validity.valid,
                    btnDisabled: btn ? btn.disabled : null,
                    bodyText: (document.body.innerText || '').slice(0, 300),
                };
            }"""
        )
        print(f"  Continue stayed disabled; diag={diag}")
        print("  Tip: if domain is blocked, change duckmail.domain (niceground.shop / stoneground.shop)")
        return False

    # 等跳到 login.nvgs.nvidia.com|cn，再等到 create-account 或密码框
    deadline = time.time() + 60
    saw_nvgs = False
    while time.time() < deadline:
        url = page.url
        if "login.nvgs.nvidia." in url:
            saw_nvgs = True
            if "create-account" in url:
                print(f"  navigated to: {url[:90]}")
                return True
            if await page.locator("#registration_password").count() > 0:
                print(f"  password form ready: {url[:90]}")
                return True
        await asyncio.sleep(1)

    if "login.nvgs.nvidia." in page.url:
        print(f"  navigated to: {page.url[:90]}")
        return True
    print(f"  unexpected url after email: {page.url[:90]}")
    return False

async def register_account(
    page: Page,
    inbox,
    password: str,
    email_provider: TempEmailProvider,
    captcha_solver,
    config: AppConfig,
) -> bool:
    """create-account 页：填密码 → 过 hCaptcha → 点 #register_button → 验证码页真实键盘输入。"""
    # [1/4] 等待密码字段并填写
    print("\n[1/4] Fill password...")
    try:
        await page.locator("#registration_password").wait_for(state="visible", timeout=30000)
    except PlaywrightTimeoutError:
        print("  password field never appeared")
        await _print_clickable_snapshot(page)
        return False

    await page.fill("#registration_password", password)
    await page.fill("#registration_passwordConfirm", password)
    # 保持登录（可选）
    try:
        checkbox = page.locator("#stay_signin_checkbox_v2-input")
        if await checkbox.count() > 0 and not await checkbox.is_checked():
            await checkbox.check()
    except Exception:
        pass
    # 勾选用户协议和隐私政策
    for sel in ["agreeeTermsAndConditions", "chinaPIPLdataGeneralAgreement"]:
        try:
            cb = page.locator(f"[formcontrolname='{sel}']").locator("mat-checkbox, mat-checkbox-wrapper, label, .mat-mdc-checkbox-inner")
            if await cb.count() > 0 and not await cb.is_checked():
                await cb.first.check()
                print(f"  checked: {sel}")
        except Exception:
            pass
    print("  password OK")

    # [2/4] 过 hCaptcha 并提交。token 被后端拒绝时页面只弹一个笼统的错误提示
    # （表现为“网络问题”），仍停在 create-account 页，所以这里换一个新 token 重试。
    if not await _solve_captcha_and_submit(page, captcha_solver, config):
        return False

    # [3/4] 等待验证码邮件。同步轮询放到线程里跑，否则会阻塞事件循环，
    # 让 Playwright 在长达 timeout_seconds 的时间内无法处理页面事件。
    print("\n[3/4] Waiting for verification code email...")
    used_codes: set[str] = set()
    code = await asyncio.to_thread(
        email_provider.poll_verification_code,
        inbox,
        config.captcha.timeout_seconds,
        used_codes,
    )
    if not code:
        print("  No verification code received")
        return False
    print(f"  Code: {code}")

    # 等验证码输入页出现（6 个 number 输入框）
    if not await _wait_for_verification_inputs(page, timeout_seconds=45):
        print("  verification inputs not detected")
        await _print_clickable_snapshot(page)
        return False

    # [4/4] 真实键盘输入验证码（React 受控组件，JS setValue 无效）
    # 验证码可能过期或输错，检测到错误提示时请求新验证码并重试
    print("\n[4/4] Type verification code...")
    for code_attempt in range(1, VERIFICATION_CODE_ATTEMPTS + 1):
        if code_attempt > 1:
            print(f"\n  请求新验证码... (第 {code_attempt}/{VERIFICATION_CODE_ATTEMPTS} 次)")
            used_codes.add(code)
            if not await _request_new_verification_code(page):
                print("  无法请求新验证码（未找到重发链接）")
                await _print_clickable_snapshot(page)
                return False

            code = await asyncio.to_thread(
                email_provider.poll_verification_code,
                inbox,
                config.captcha.timeout_seconds,
                used_codes,
            )
            if not code:
                print("  未收到新验证码（已排除旧码）")
                return False
            print(f"  新验证码: {code}")

        if not await _type_verification_code(page, code):
            print("  failed to type verification code")
            return False

        # 点"继续"提交验证码
        await _click_continue(page)
        result = await _wait_verification_submit_result(page, timeout_seconds=20)
        if result == "error":
            print("  验证码无效，准备重新请求")
            continue
        if result == "ok":
            # 验证码通过后 NVIDIA 会插入"创建通行密钥"引导页，先跳过它
            await _skip_passkey_prompt_if_present(page)
            print("\nRegistration submitted!")
            return True

        # 超时仍停在验证页：当作失败重试
        print("  提交后仍停在验证码页，当作失败重试")
        continue

    print("  验证码尝试次数已用尽")
    return False

# hCaptcha token 被后端拒绝时的重试次数（每次都会重新求解一个新 token）
CAPTCHA_SUBMIT_ATTEMPTS = 3
VERIFICATION_CODE_ATTEMPTS = 4

async def _solve_captcha_and_submit(page: Page, captcha_solver, config: AppConfig) -> bool:
    """过 hCaptcha 并点 #register_button，直到后端受理注册。

    NVIDIA 的注册接口是 POST /api/1/frontend/oauth/user/register，请求体里带
    validation.response（hCaptcha token）。token 校验不通过时接口返回非 2xx，
    前端只弹一个笼统的错误提示（看起来像网络问题），页面仍停在 create-account。
    这里直接监听该接口的状态码来判定成败，失败就换新 token 重来。
    """
    for attempt in range(1, CAPTCHA_SUBMIT_ATTEMPTS + 1):
        if attempt > 1:
            print(f"\n  重新求解 hCaptcha 并重试提交 (第 {attempt}/{CAPTCHA_SUBMIT_ATTEMPTS} 次)...")
            await _reset_hcaptcha_widget(page)

        try:
            solved = await captcha_solver.solve(page)
        except Exception as exc:
            # 打码平台不可用（网络超时、额度耗尽等）只应让当前账号失败
            print(f"  Captcha solver error: {exc}")
            solved = False
        if not solved:
            print("  Captcha failed")
            continue

        print(f"\n[2/4] Submit registration (#register_button)... (attempt {attempt})")
        register_result = await _click_register_and_wait_result(page)

        if register_result == "accepted":
            return True
        if register_result == "email_exists":
            # 邮箱已被占用，换 token 也没用
            print("  该邮箱已注册，放弃当前账号")
            return False
        print(f"  注册提交未被受理 ({register_result})")

    print(f"  连续 {CAPTCHA_SUBMIT_ATTEMPTS} 次提交均失败")
    await _print_clickable_snapshot(page)
    return False

async def _click_register_and_wait_result(page: Page) -> str:
    """点 #register_button 并等 user/register 接口响应，返回判定结果。

    返回值: "accepted" | "email_exists" | "rejected" | "no_response" | "not_clickable"
    """
    register_btn = page.locator("#register_button")
    try:
        await register_btn.wait_for(state="visible", timeout=15000)
        for _ in range(30):
            if await register_btn.is_enabled():
                break
            await asyncio.sleep(1)
        else:
            return "not_clickable"
    except Exception as exc:
        print(f"  #register_button not clickable: {exc}")
        return "not_clickable"

    # 先挂上等待器再点击，避免响应比监听更快
    response_waiter = asyncio.create_task(_wait_for_register_response(page))
    try:
        await register_btn.click()
    except Exception as exc:
        response_waiter.cancel()
        print(f"  #register_button click failed: {exc}")
        return "not_clickable"

    return await response_waiter

async def _wait_for_register_response(page: Page, timeout_seconds: int = 45) -> str:
    """等 POST .../oauth/user/register 的响应，按状态码判定注册是否被受理。"""
    try:
        response = await page.wait_for_event(
            "response",
            predicate=lambda resp: "oauth/user/register" in resp.url and resp.request.method == "POST",
            timeout=timeout_seconds * 1000,
        )
    except Exception:
        return "no_response"

    if response.status in (200, 201, 204):
        print(f"  register accepted ({response.status})")
        return "accepted"

    body = ""
    try:
        body = (await response.text())[:300]
    except Exception:
        pass
    print(f"  register rejected ({response.status}): {body}")
    if "CONFLICT" in body or "ALREADY" in body.upper():
        return "email_exists"
    return "rejected"

async def _reset_hcaptcha_widget(page: Page) -> None:
    """清掉上一次注入的 token 并重置 hCaptcha 组件，以便注入新 token。"""
    try:
        await page.evaluate(
            """() => {
                window.__hCaptchaInjectedToken = null;
                if (window.hcaptcha && typeof window.hcaptcha.reset === 'function') {
                    try { window.hcaptcha.reset(); } catch (_) {}
                }
            }"""
        )
    except Exception:
        pass
    await asyncio.sleep(2)

async def _wait_for_verification_inputs(page: Page, timeout_seconds: int) -> bool:
    """等待 6 个验证码数字输入框出现。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if await page.locator('input[type="number"]').count() >= 6:
            print("  verification inputs appeared")
            return True
        await asyncio.sleep(1)
    return False

async def _type_verification_code(page: Page, code: str) -> bool:
    """点第一个数字框后逐字符键盘输入，触发 React 状态。"""
    inputs = page.locator('input[type="number"]')
    count = await inputs.count()
    if count < 6:
        return False
    # 先清空旧内容（重试时必须），再逐格输入
    for index in range(min(count, 6)):
        try:
            box = inputs.nth(index)
            await box.click()
            await box.fill("")
        except Exception:
            pass
    await inputs.first.click()
    for index, digit in enumerate(code[: min(count, 6)]):
        try:
            await inputs.nth(index).click()
        except Exception:
            pass
        await page.keyboard.type(digit, delay=80)
        await asyncio.sleep(0.15)
    await asyncio.sleep(0.5)
    return True

async def _click_continue(page: Page) -> bool:
    """点验证码/同意页的主推进按钮。

    页面语言随 locale 变化，所以中英文名称都试一遍。
    """
    clicked = await _click_button_by_names(page, ("继续", "提交", "Continue", "Submit"))
    if clicked:
        print(f"  clicked [{clicked}]")
        return True
    return False

async def _click_button_by_names(page: Page, names: tuple[str, ...], timeout_ms: int = 5000) -> str | None:
    """按可访问名依次尝试点击可见且可用的按钮，返回命中的名称。"""
    for name in names:
        try:
            button = page.get_by_role("button", name=name).filter(visible=True).first
            if await button.count() > 0 and await button.is_enabled():
                await button.click(timeout=timeout_ms)
                return name
        except Exception:
            continue
    return None

# 跳过通行密钥的二次确认对话框上的"确定"按钮
_CONFIRM_BUTTON_NAMES = ("确定", "OK", "Confirm", "Yes", "是")

async def _confirm_skip_dialog(page: Page) -> bool:
    """点掉"确定要跳过设置通行密钥吗"确认对话框。"""
    for _ in range(10):
        clicked = await _click_button_by_names(page, _CONFIRM_BUTTON_NAMES, timeout_ms=3000)
        if clicked:
            print(f"  passkey 确认对话框：已点击 [{clicked}]")
            return True
        await asyncio.sleep(0.5)
    return False

async def _has_verification_code_error(page: Page) -> bool:
    """检测页面上是否显示"验证码无效"的错误提示。"""
    try:
        # 注意：必须用连续子串。"invalid code" 匹配不到 "Invalid verification code"
        error_texts = (
            "Invalid verification code",
            "invalid verification code",
            "Try entering your verification code again",
            "验证码无效",
            "验证码错误",
            "验证码不正确",
            "incorrect verification code",
            "code is invalid",
            "wrong code",
        )
        for text in error_texts:
            if await page.get_by_text(text, exact=False).count() > 0:
                return True
        # 兜底：扫描可见文本
        body = (await page.locator("body").inner_text(timeout=2000)).lower()
        needles = (
            "invalid verification code",
            "验证码无效",
            "验证码错误",
            "incorrect verification code",
        )
        return any(n in body for n in needles)
    except Exception:
        return False

async def _wait_verification_submit_result(page: Page, timeout_seconds: int = 20) -> str:
    """提交验证码后等待结果：ok / error / unknown。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if await _has_verification_code_error(page):
            return "error"
        otp_count = await page.locator('input[type="number"]').count()
        url = page.url.lower()
        left_otp = otp_count < 6
        progressed = any(
            token in url
            for token in (
                "passkey",
                "consent",
                "select-account",
                "cloudaccounts",
                "prompt-setup",
                "org",
            )
        )
        if left_otp or progressed:
            # 短暂再确认不是闪一下错误
            await asyncio.sleep(0.8)
            if await _has_verification_code_error(page):
                return "error"
            return "ok"
        await asyncio.sleep(0.5)
    if await _has_verification_code_error(page):
        return "error"
    if await page.locator('input[type="number"]').count() < 6:
        return "ok"
    return "unknown"

async def _request_new_verification_code(page: Page) -> bool:
    """点击「重新获取验证码」链接（中英文文案都覆盖）。"""
    try:
        # 截图里的文案：Didn't get the code? Request a new one.
        link_texts = (
            "Request a new one",
            "Didn't get the code",
            "request a new one",
            "请求新验证码",
            "重新请求新验证码",
            "重新发送验证码",
            "重新获取验证码",
            "获取新验证码",
            "request new code",
            "resend code",
            "Resend",
        )
        for text in link_texts:
            link = page.get_by_text(text, exact=False).filter(visible=True).first
            if await link.count() > 0:
                await link.click(timeout=5000)
                print(f"  已点击重发链接 [{text}]")
                await asyncio.sleep(2)
                return True

        # 再试 link / button role
        for name in ("Request a new one", "Resend", "重新发送", "请求新验证码"):
            for role in ("link", "button"):
                try:
                    loc = page.get_by_role(role, name=re.compile(name, re.I)).filter(visible=True).first
                    if await loc.count() > 0:
                        await loc.click(timeout=5000)
                        print(f"  已点击重发 [{role}:{name}]")
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue
        return False
    except Exception as exc:
        print(f"  点击请求新验证码失败: {exc}")
        return False

async def _skip_passkey_prompt(page: Page) -> bool:
    """跳过"创建通行密钥"引导页（/v1/passkey/prompt-setup）。

    NVIDIA 在邮箱验证之后新增了这一步，页面上有两个按钮：
      #cancelSetupSelect_btn  → "稍后再说"（我们要点的）
      #setUpPasskey_btn       → "立即创建"（会拉起 WebAuthn，自动化环境无法完成）

    点"稍后再说"之后还会弹一个确认对话框（"您确定要跳过设置通行密钥吗？"），
    必须再点"确定"才会真正离开该页。
    """
    try:
        skip_btn = page.locator("#cancelSetupSelect_btn")
        if await skip_btn.count() > 0:
            await skip_btn.first.click(timeout=5000)
            print("  passkey 引导页：已点击 [稍后再说]")
            await _confirm_skip_dialog(page)
            return True
    except Exception:
        pass

    clicked = await _click_button_by_names(page, ("稍后再说", "Maybe later", "Not now", "Skip"))
    if clicked:
        print(f"  passkey 引导页：已点击 [{clicked}]")
        await _confirm_skip_dialog(page)
        return True

    print("  passkey 引导页：未找到跳过按钮")
    await _print_clickable_snapshot(page)
    return False

async def _skip_passkey_prompt_if_present(page: Page, wait_seconds: int = 15) -> bool:
    """等待并跳过可能出现的通行密钥引导页。没出现就直接返回。"""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if "passkey" in page.url:
            return await _skip_passkey_prompt(page)
        await asyncio.sleep(1)
    return False

async def _wait_for_url_change(page: Page, current_url: str, wait_seconds: int) -> bool:
    """等页面自行跳走。返回 True 表示 URL 已变化。"""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        await asyncio.sleep(1)
        if page.url != current_url:
            return True
    return False

async def _recover_from_navigation_error(page: Page) -> bool:
    """从 chrome-error 页恢复：先试重新加载，再退回 build.nvidia.com。

    此时账号已注册且 session cookie 已在浏览器里，重新打开站点即可让
    NGC 的 user-context 探测重新生效，不需要重新登录。
    """
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        if not page.url.startswith("chrome-error"):
            return True
    except Exception:
        pass

    try:
        await page.goto("https://ngc.nvidia.com/signin", wait_until="domcontentloaded", timeout=60000)
        return not page.url.startswith("chrome-error")
    except Exception as exc:
        print(f"  恢复导航失败: {exc}")
        return False

async def _ensure_hcaptcha_hook(page: Page) -> None:
    """用 page.add_init_script 在所有后续页面加载前注册 hCaptcha 拦截器。

    hCaptcha render=explicit 模式下，Angular 组件在 hCaptchaLoad 回调中调用
    hcaptcha.render(el, {callback: onSuccess})。hcaptcha.render 内部存储回调。
    必须在 hCaptcha API 脚本创建 window.hcaptcha 时拦截，包装 render 方法，
    在回调注册时捕获到 window.__hCaptchaCallback。

    自动化浏览器里 hCaptcha 自身的挑战常常失败（组件显示"请再试一次"），
    随后会触发 expired-callback / error-callback，让 Angular 清掉我们注入的
    token 并重新禁用 #register_button。这里把这两个回调拦下来：token 注入后
    不再让它们生效，同时让 getResponse() 返回注入的 token，保证 Angular 在
    提交时读到的是有效值。
    """
    await page.add_init_script(
        r"""(() => {
            window.__hCaptchaInjectedToken = null;

            const suppressWhenInjected = (originalCallback, callbackName) => function(...args) {
                if (window.__hCaptchaInjectedToken) {
                    console.debug('[hook] suppressed hCaptcha ' + callbackName);
                    return undefined;
                }
                return originalCallback.apply(this, args);
            };

            // 拦截 hCaptcha API 脚本创建 window.hcaptcha 对象
            let _realHcaptcha = null;
            Object.defineProperty(window, 'hcaptcha', {
                configurable: true,
                enumerable: true,
                get() { return _realHcaptcha; },
                set(val) {
                    _realHcaptcha = val;
                    if (!val) { return; }

                    if (typeof val.render === 'function') {
                        const origRender = val.render.bind(val);
                        val.render = function(el, opts) {
                            if (opts && typeof opts.callback === 'function') {
                                window.__hCaptchaCallback = opts.callback;
                            }
                            // 组件自身挑战失败时不允许清掉已注入的 token
                            if (opts && typeof opts['expired-callback'] === 'function') {
                                opts['expired-callback'] = suppressWhenInjected(
                                    opts['expired-callback'], 'expired-callback'
                                );
                            }
                            if (opts && typeof opts['error-callback'] === 'function') {
                                opts['error-callback'] = suppressWhenInjected(
                                    opts['error-callback'], 'error-callback'
                                );
                            }
                            if (opts && typeof opts['chalexpired-callback'] === 'function') {
                                opts['chalexpired-callback'] = suppressWhenInjected(
                                    opts['chalexpired-callback'], 'chalexpired-callback'
                                );
                            }
                            return origRender(el, opts);
                        };
                    }

                    // Angular 提交前可能改用 getResponse() 取值
                    if (typeof val.getResponse === 'function') {
                        const origGetResponse = val.getResponse.bind(val);
                        val.getResponse = function(...args) {
                            if (window.__hCaptchaInjectedToken) {
                                return window.__hCaptchaInjectedToken;
                            }
                            return origGetResponse(...args);
                        };
                    }
                }
            });
        })()"""
    )

async def _print_clickable_snapshot(page: Page) -> None:
    buttons = await page.evaluate(
        r"""() => Array.from(document.querySelectorAll(
            'button, [role="button"], input[type="button"], input[type="submit"]'
        )).slice(0, 20).map((element) => ({
            text: [element.innerText, element.textContent, element.value, element.getAttribute('aria-label')]
                .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim(),
            disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
            visible: window.getComputedStyle(element).display !== 'none' && element.getClientRects().length > 0
        }))"""
    )
    print("  clickable snapshot:")
    print(json.dumps(buttons, ensure_ascii=False, indent=2))

# ---------------------------------------------------------------------------
#  阶段 C：注册后跳转 + 建 key
# ---------------------------------------------------------------------------

async def finalize_and_create_key(page: Page, config: AppConfig) -> str | None:
    """注册提交后依次处理页面跳转，直到 session 有效并建 key。

    真实跳转链（实测确认）：
      验证码提交 → passkey/prompt-setup 页(点"稍后再说") → signin-redirect
      → consent 页(点"提交") → select-account(填组织名)
      → complete-profile(session 已有效, 直接建 key)
    """
    print("\n[阶段C] 处理注册后跳转，直到 session 有效...")
    deadline = time.time() + 240
    last_url = ""
    org_fail_count = 0

    while time.time() < deadline:
        # 每轮先尝试直接建 key（session 可能已经有效）
        org_name = await _get_org_name(page)
        if org_name:
            print(f"  session 有效，orgName: {org_name}")
            return await _create_key_in_browser(page, org_name, config)

        url_now = page.url
        if url_now != last_url:
            print(f"  当前页面: {url_now[:90]}")
            last_url = url_now

        # 跳转链中某一跳加载失败会停在 chrome-error 页，只能重新导航把流程接回去
        if url_now.startswith("chrome-error"):
            print("  页面加载失败，重新打开 NGC signin 以恢复流程...")
            if not await _recover_from_navigation_error(page):
                await asyncio.sleep(3)
            last_url = ""
            continue

        # 通行密钥引导页 → 点"稍后再说"跳过。
        # 跳过动作已在 register_account 里做过，此处 URL 可能只是还没来得及跳转，
        # 所以先给它一点时间自行离开，避免重复点击与无意义的告警。
        if "passkey" in url_now:
            if await _wait_for_url_change(page, url_now, wait_seconds=5):
                continue
            await _skip_passkey_prompt(page)
            await asyncio.sleep(3)
            continue

        # 创建组织页（利用组织名跳过手机验证）
        if "select-account" in url_now or "cloudaccounts.nvidia.com" in url_now:
            await _wait_for_select_account_ready(page)
            org_label = _unique_org_name(config.nvidia.account_name)
            print(f"  创建组织页：填组织名... ({org_label})")
            ok = await _create_org(page, org_label)
            if not ok:
                org_fail_count += 1
                print(f"  创建组织未成功 (attempt {org_fail_count})")
                if org_fail_count >= 2:
                    await _print_clickable_snapshot(page)
                if org_fail_count >= 5:
                    print("  创建组织多次失败，放弃建 key")
                    return None
                await asyncio.sleep(3)
            else:
                org_fail_count = 0
                # 等页面离开 select-account
                changed = await _wait_for_url_change(page, url_now, wait_seconds=25)
                if not changed:
                    print("  提交后仍停在组织页，继续重试...")
                    org_fail_count += 1
            continue

        # consent 页 → 点提交
        if "consent" in url_now or "static-login.nvidia.com" in url_now:
            print("  consent 页：点提交...")
            await _click_continue(page)
            await asyncio.sleep(3)
            continue

        # signin-redirect、complete-profile 等 → 等待跳转
        await asyncio.sleep(2)

    print("  阶段C 超时，未能建 key")
    return None

async def _get_org_name(page: Page) -> str | None:
    """在浏览器上下文内 fetch user-context（credentials:include），拿 orgName。"""
    try:
        result = await page.evaluate(
            """async () => {
                try {
                    const resp = await fetch('https://api.ngc.nvidia.com/user-context', {
                        credentials: 'include',
                        headers: {'accept': 'application/json'}
                    });
                    if (!resp.ok) return {ok: false, status: resp.status};
                    const data = await resp.json();
                    return {ok: true, orgName: data.orgName || null};
                } catch (e) {
                    return {ok: false, error: String(e)};
                }
            }"""
        )
    except Exception:
        return None
    if result and result.get("ok"):
        return result.get("orgName")
    return None

async def _create_key_in_browser(page: Page, org_name: str, config: AppConfig) -> str | None:
    """在浏览器上下文内 POST 建 key（credentials:include），返回 nvapi-... key。"""
    print("  POST /keys/type/AI_PLAYGROUNDS_KEY...")
    payload = {
        "expiryDate": config.nvidia.key_expiry_date,
        "name": config.nvidia.key_name,
        "type": "AI_PLAYGROUNDS_KEY",
        "policies": [
            {
                "product": "nv-cloud-functions",
                "scopes": ["invoke_function"],
                "resources": [{"id": "*", "type": "account-functions"}],
            }
        ],
    }
    result = await page.evaluate(
        """async ({orgName, payload}) => {
            try {
                const resp = await fetch(
                    `https://api.ngc.nvidia.com/v3/orgs/${orgName}/keys/type/AI_PLAYGROUNDS_KEY`,
                    {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'content-type': 'application/json', 'accept': '*/*'},
                        body: JSON.stringify(payload)
                    }
                );
                const text = await resp.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) {}
                return {status: resp.status, data, text: text.slice(0, 300)};
            } catch (e) {
                return {status: 0, error: String(e)};
            }
        }""",
        {"orgName": org_name, "payload": payload},
    )

    status = result.get("status")
    if status not in (200, 201):
        print(f"  建 key 失败: {status}: {result.get('text') or result.get('error')}")
        return None

    data = result.get("data") or {}
    api_key = (
        (data.get("apiKey") or {}).get("value", "")
        or (data.get("result") or {}).get("apiKey", {}).get("value", "")
    )
    if api_key:
        print(f"\nAI_PLAYGROUNDS_KEY: {api_key}")
        return api_key
    print("  响应中未找到 apiKey.value")
    return None

def _unique_org_name(base: str) -> str:
    """生成更易通过校验的组织名（避免空格/保留字，附加随机后缀）。"""
    cleaned = "".join(ch for ch in (base or "org") if ch.isalnum())[:10] or "org"
    # 避免名字里带 nvidia（容易触发保留字校验）
    if "nvidia" in cleaned.lower():
        cleaned = "org"
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"{cleaned}{suffix}"


async def _wait_for_select_account_ready(page: Page, timeout_seconds: int = 30) -> bool:
    """等 cloudaccounts/select-account 真正渲染出输入框或按钮。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            # 页面若空白，尝试等网络并轻量刷新一次
            has_input = await page.locator("input:visible").count() > 0
            has_button = await page.locator("button:visible").count() > 0
            if has_input or has_button:
                print("  select-account UI ready")
                return True
        except Exception:
            pass
        await asyncio.sleep(1)

    print("  select-account still empty, reloading once...")
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
    except Exception as exc:
        print(f"  reload failed: {exc}")
    try:
        return (await page.locator("input:visible, button:visible").count()) > 0
    except Exception:
        return False


async def _create_org(page: Page, org_name: str) -> bool:
    """在 select-account 页填组织名并创建（跳过手机验证的关键）。"""
    # cloudaccounts 有时把表单放在 iframe 里
    contexts = [page] + list(page.frames)

    async def _find_org_input(ctx):
        selectors = [
            'input[placeholder*="Organization" i]',
            'input[placeholder*="organization" i]',
            'input[placeholder*="Account" i]',
            'input[placeholder*="账户" i]',
            'input[placeholder*="帐户" i]',
            'input[placeholder*="组织" i]',
            'input[aria-label*="Organization" i]',
            'input[name*="org" i]',
            'input[name*="account" i]',
            'input[type="text"]:visible',
        ]
        for sel in selectors:
            loc = ctx.locator(sel).first
            try:
                if await loc.count() > 0 and await loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    # 展开「创建新账号」入口（仅当还没有输入框时）
    for ctx in contexts:
        inp = await _find_org_input(ctx)
        if inp is not None:
            break
        for name in (
            "Create NVIDIA Cloud Account",
            "Create a new NVIDIA Cloud Account",
            "Create new account",
            "Create account",
            "创建 NVIDIA Cloud Account",
            "创建新的 NVIDIA Cloud Account",
            "创建新账号",
            "创建帐户",
            "创建账户",
        ):
            try:
                starter = ctx.get_by_role("button", name=name).filter(visible=True).first
                if await starter.count() == 0:
                    starter = ctx.get_by_text(name, exact=False).filter(visible=True).first
                if await starter.count() == 0:
                    continue
                await starter.click(timeout=3000)
                print(f"  opened create form via [{name}]")
                await asyncio.sleep(1.5)
                break
            except Exception:
                continue

    text_input = None
    input_ctx = page
    for ctx in [page] + list(page.frames):
        text_input = await _find_org_input(ctx)
        if text_input is not None:
            input_ctx = ctx
            break

    if text_input is None:
        print("  org name input not found")
        return False

    await text_input.click(force=True)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(org_name, delay=40)
    try:
        await text_input.press("Tab")
    except Exception:
        pass
    await asyncio.sleep(1.0)
    try:
        print(f"  org input value: {await text_input.input_value()}")
    except Exception:
        pass

    button_names = (
        "Create NVIDIA Cloud Account",
        "Create account",
        "Create",
        "Continue",
        "Next",
        "Submit",
        "创建 NVIDIA Cloud Account",
        "创建帐户",
        "创建账户",
        "创建",
        "继续",
        "下一步",
        "提交",
    )
    for ctx in [input_ctx, page] + list(page.frames):
        for name in button_names:
            try:
                btn = ctx.get_by_role("button", name=name).filter(visible=True).first
                if await btn.count() == 0:
                    continue
                for _ in range(25):
                    if await btn.is_enabled():
                        await btn.click()
                        print(f"  clicked [{name}]")
                        await asyncio.sleep(2)
                        return True
                    await asyncio.sleep(0.4)
            except Exception:
                continue

    # 兜底：JS 点击可用主按钮
    try:
        clicked = await page.evaluate(
            """() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const btn = buttons.find(b => {
                    const style = window.getComputedStyle(b);
                    const visible = style.display !== 'none' && b.getClientRects().length > 0;
                    return visible && !b.disabled && /create|创建|continue|继续|next|下一步|submit|提交/i.test(
                        (b.innerText || b.textContent || '')
                    );
                });
                if (!btn) return null;
                btn.click();
                return (btn.innerText || '').replace(/\\s+/g, ' ').trim();
            }"""
        )
        if clicked:
            print(f"  clicked fallback [{clicked}]")
            await asyncio.sleep(2)
            return True
    except Exception:
        pass

    print("  create-org button not clickable")
    return False


async def _close_browser(browser, delay: int) -> None:
    print(f"\nBrowser will close in {delay} seconds...")
    await asyncio.sleep(delay)
    await browser.close()

if __name__ == "__main__":
    main_cli()
