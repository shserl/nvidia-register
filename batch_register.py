#!/usr/bin/env python3
"""
nvidia-register 多标签页并发版
- 多个标签页同时注册
- 用户手动过hCaptcha
- 脚本自动完成其余所有步骤（邮箱创建、密码、验证码、建key）
"""

import asyncio
import csv
import secrets
import signal
import string
import sys
import time
from pathlib import Path
from playwright.async_api import Page, async_playwright

from config import AppConfig, describe_config, init_config, load_config
from captcha import reset_captcha_state, start_capturing_sitekey
from email_providers import TempEmailProvider, build_email_provider
from passwords import generate_password
from records import append_account_record

FIELDNAMES = ["email", "password", "apikey"]

async def register_one(p, config: AppConfig, email_provider: TempEmailProvider, account_id: int, total: int) -> str | None:
    password = generate_password(12)
    reset_captcha_state()

    browser = await p.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    page = await browser.new_page(viewport={"width": 1280, "height": 800})

    try:
        rand_letters = "".join(secrets.choice(string.ascii_lowercase) for _ in range(4))
        inbox_name = f"nv{rand_letters}{str(int(time.time()))[-6:]}"
        inbox = email_provider.create_inbox(inbox_name)
        print(f"[{account_id}/{total}] 邮箱: {inbox.address}")

        await page.goto("https://build.nvidia.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        try:
            btn = page.locator("#onetrust-accept-btn-handler")
            await btn.wait_for(state="visible", timeout=5000)
            await btn.click()
            await asyncio.sleep(1)
        except: pass

        login = page.get_by_role("button", name="Login").first
        await login.wait_for(state="visible", timeout=10000)
        await login.click()
        try: await page.locator('input[name="email"]').first.wait_for(state="visible", timeout=8000)
        except: pass
        await asyncio.sleep(3)

        email_input = page.locator('input[name="email"]:visible').first
        await email_input.click()
        await email_input.press_sequentially(inbox.address, delay=50)
        await asyncio.sleep(0.3)
        next_btn = page.get_by_role("button", name="Next").filter(visible=True).first
        for _ in range(20):
            if await next_btn.is_enabled(): break
            await asyncio.sleep(0.5)
        await next_btn.click()
        try: await page.wait_for_url("**/login.nvgs.nvidia.com/**", timeout=20000)
        except: pass

        if "create-account" not in page.url:
            print(f"  ✗ [{account_id}] 未进入create-account")
            return None

        await page.locator("#registration_password").wait_for(state="visible", timeout=15000)
        await page.fill("#registration_password", password)
        await page.fill("#registration_passwordConfirm", password)
        print(f"  [{account_id}] 密码已填，请在浏览器中完成hCaptcha...")

        start_capturing_sitekey(page)

        for i in range(300):
            btn = page.locator("#register_button")
            if await btn.count() > 0 and await btn.is_enabled():
                print(f"  [{account_id}] hCaptcha完成! ({i}s)")
                break
            await asyncio.sleep(1)
        else:
            print(f"  ✗ [{account_id}] hCaptcha超时")
            return None

        await page.locator("#register_button").click()
        print(f"  [{account_id}] 注册已提交，等待验证码邮件...")

        code = await asyncio.to_thread(email_provider.poll_verification_code, inbox, 120)
        if not code:
            print(f"  ✗ [{account_id}] 未收到验证码")
            return None
        print(f"  [{account_id}] 验证码: {code}")

        used_codes: set[str] = set()
        for attempt in range(1, 5):
            if attempt > 1:
                print(f"  [{account_id}] 验证码无效，请求新验证码... ({attempt}/4)")
                used_codes.add(code)
                if not await _request_new_verification_code(page):
                    print(f"  ✗ [{account_id}] 找不到重发链接")
                    return None
                code = await asyncio.to_thread(
                    email_provider.poll_verification_code, inbox, 120, used_codes
                )
                if not code:
                    print(f"  ✗ [{account_id}] 未收到新验证码")
                    return None
                print(f"  [{account_id}] 新验证码: {code}")

            inputs = page.locator('input[type="number"]')
            count = await inputs.count()
            if count >= 6:
                for i in range(6):
                    try:
                        box = inputs.nth(i)
                        await box.click()
                        await box.fill("")
                    except Exception:
                        pass
                await inputs.first.click()
                for i, digit in enumerate(code[:6]):
                    await inputs.nth(i).click()
                    await page.keyboard.type(digit, delay=80)
                    await asyncio.sleep(0.15)

            await _click_continue(page)
            await asyncio.sleep(4)

            if await _has_verification_code_error(page):
                continue
            if await page.locator('input[type="number"]').count() >= 6:
                # 仍停在验证页且无明确错误文案时也重试
                body = (await page.locator("body").inner_text(timeout=2000)).lower()
                if "invalid verification" in body or "验证码无效" in body:
                    continue
                # 若 Continue 后仍在 OTP，再等一会
                await asyncio.sleep(3)
                if await _has_verification_code_error(page) or await page.locator('input[type="number"]').count() >= 6:
                    url = page.url.lower()
                    if "passkey" not in url and "consent" not in url and "select-account" not in url:
                        continue
            break

        await _skip_passkey_if_present(page)

        api_key = await _finalize(page, config)
        if api_key:
            append_account_record(config.nvidia.output_csv, inbox.address, password, api_key)
            print(f"  ✓ [{account_id}] {inbox.address} → {api_key[:30]}...")
        return api_key

    except Exception as e:
        print(f"  ✗ [{account_id}] 异常: {e}")
        return None
    finally:
        await _close_browser(browser, 3)


async def _click_continue(page: Page):
    for name in ("继续", "提交", "Continue", "Submit"):
        try:
            btn = page.get_by_role("button", name=name).filter(visible=True).first
            if await btn.count() > 0 and await btn.is_enabled():
                await btn.click(timeout=5000)
                return
        except: continue

async def _has_verification_code_error(page: Page) -> bool:
    try:
        for text in (
            "Invalid verification code",
            "invalid verification code",
            "Try entering your verification code again",
            "验证码无效",
            "验证码错误",
            "incorrect verification code",
        ):
            if await page.get_by_text(text, exact=False).count() > 0:
                return True
        body = (await page.locator("body").inner_text(timeout=2000)).lower()
        return "invalid verification code" in body or "验证码无效" in body
    except Exception:
        return False

async def _request_new_verification_code(page: Page) -> bool:
    for text in (
        "Request a new one",
        "Didn't get the code",
        "请求新验证码",
        "重新请求新验证码",
        "重新发送验证码",
        "request new code",
        "resend code",
    ):
        try:
            link = page.get_by_text(text, exact=False).filter(visible=True).first
            if await link.count() > 0:
                await link.click(timeout=5000)
                await asyncio.sleep(2)
                return True
        except Exception:
            continue
    return False

async def _skip_passkey_if_present(page: Page):
    try:
        skip = page.locator("#cancelSetupSelect_btn")
        if await skip.count() > 0:
            await skip.first.click(timeout=5000)
            await asyncio.sleep(2)
            for name in ("确定", "OK", "Confirm"):
                try:
                    btn = page.get_by_role("button", name=name).filter(visible=True).first
                    if await btn.count() > 0:
                        await btn.click(timeout=3000)
                        break
                except: continue
            await asyncio.sleep(3)
    except: pass

async def _finalize(page: Page, config: AppConfig) -> str | None:
    deadline = time.time() + 240
    while time.time() < deadline:
        org = await _get_org_name(page)
        if org:
            return await _create_key(page, org, config)

        url = page.url
        if "select-account" in url or "cloudaccounts.nvidia.com" in url:
            inp = page.locator('input[type="text"]:visible').first
            if await inp.count() > 0:
                await inp.fill(config.nvidia.account_name)
                await asyncio.sleep(0.5)
                try:
                    create_btn = page.get_by_role("button", name="Create NVIDIA Cloud Account").first
                    await create_btn.wait_for(state="visible", timeout=5000)
                    await create_btn.click()
                    await asyncio.sleep(4)
                except: pass
            continue
        if "consent" in url or "static-login.nvidia.com" in url:
            await _click_continue(page)
            await asyncio.sleep(3)
            continue
        await asyncio.sleep(2)
    return None

async def _get_org_name(page: Page) -> str | None:
    try:
        result = await page.evaluate("""async () => {
            try {
                const r = await fetch('https://api.ngc.nvidia.com/user-context', {
                    credentials: 'include', headers: {'accept': 'application/json'}
                });
                if (!r.ok) return null;
                const d = await r.json();
                return d.orgName || null;
            } catch(e) { return null; }
        }""")
        return result
    except: return None

async def _create_key(page: Page, org_name: str, config: AppConfig) -> str | None:
    payload = {
        "expiryDate": config.nvidia.key_expiry_date,
        "name": config.nvidia.key_name,
        "type": "AI_PLAYGROUNDS_KEY",
        "policies": [{"product": "nv-cloud-functions", "scopes": ["invoke_function"], "resources": [{"id": "*", "type": "account-functions"}]}],
    }
    result = await page.evaluate("""async ({org, pl}) => {
        try {
            const r = await fetch(`https://api.ngc.nvidia.com/v3/orgs/${org}/keys/type/AI_PLAYGROUNDS_KEY`, {
                method: 'POST', credentials: 'include',
                headers: {'content-type': 'application/json', 'accept': '*/*'},
                body: JSON.stringify(pl)
            });
            const t = await r.text();
            let d = null; try { d = JSON.parse(t); } catch(_) {}
            return {status: r.status, data: d, text: t.slice(0, 300)};
        } catch(e) { return {status: 0, error: String(e)}; }
    }""", {"org": org_name, "pl": payload})

    if result.get("status") not in (200, 201):
        print(f"  建key失败: {result.get('text') or result.get('error')}")
        return None
    data = result.get("data") or {}
    api_key = (data.get("apiKey") or {}).get("value", "") or (data.get("result") or {}).get("apiKey", {}).get("value", "")
    return api_key

async def _close_browser(browser, delay):
    await asyncio.sleep(delay)
    await browser.close()

async def run(config: AppConfig, total: int, max_tabs: int):
    email_provider = build_email_provider(config)

    print("=" * 60)
    print(f"NVIDIA 多标签页并发注册 (最多{max_tabs}个标签页)")
    print("-" * 60)
    describe_config(config)
    print(f"  注册数量: {total}")
    print("=" * 60)
    print("  每个标签页需要手动完成hCaptcha验证")
    print("  完成后脚本自动处理验证码、建key等步骤\n")

    async with async_playwright() as p:
        sem = asyncio.Semaphore(max_tabs)
        success = [0]
        fail = [0]

        async def worker(aid):
            async with sem:
                result = await register_one(p, config, email_provider, aid, total)
                if result:
                    success[0] += 1
                else:
                    fail[0] += 1

        tasks = [asyncio.create_task(worker(i)) for i in range(1, total + 1)]
        await asyncio.gather(*tasks)

    print("\n" + "=" * 60)
    print(f"完成! 成功: {success[0]}, 失败: {fail[0]}, 总计: {total}")
    print("=" * 60)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        init_config()
        return

    config = load_config()
    total = 10
    max_tabs = 3

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] in ("-n", "--count") and i + 1 < len(sys.argv):
            total = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--tabs" and i + 1 < len(sys.argv):
            max_tabs = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i].isdigit():
            total = int(sys.argv[i])
            i += 1
        else:
            i += 1

    max_tabs = min(max_tabs, 8)

    try:
        asyncio.run(run(config, total, max_tabs))
    except KeyboardInterrupt:
        print("\n\n中断退出")


if __name__ == "__main__":
    main()
