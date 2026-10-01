# 魔改记录

> 基于 GitHub 仓库 `zseek/nvidia-register`，以下记录所有修改。

---

## 一、发现的核心BUG

### 现象
- `python main.py -n 1` 运行后，hCaptcha token 已成功注入（NoneCap返回token）
- 但 `#register_button` 按钮始终 disabled，点击无反应
- 页面报 `Captcha is required` 错误

### 根本原因（通过Angular调试发现）
**注册按钮默认 disabled，必须同时勾选两个用户协议checkbox才会 enable：**
1. `agreeeTermsAndConditions` - 用户协议同意
2. `chinaPIPLdataGeneralAgreement` - 中国个人信息保护法同意

原脚本完全没有处理这两个checkbox的勾选，所以无论hCaptcha是否通过，按钮永远是灰色的。

---

## 二、修改文件详解

### 1. `main.py`（+9行）—— 修复注册按钮不enable的bug

**修改位置：** `register_account()` 函数，密码填写完成后

**修改内容：**
```python
# 原代码只有：
#     try:
#         checkbox = page.locator("[formcontrolname='agreeeTermsAndConditions']")
#         ...
#     except Exception:
#         pass

# 改为：
for sel in ["agreeeTermsAndConditions", "chinaPIPLdataGeneralAgreement"]:
    try:
        cb = page.locator(f"[formcontrolname='{sel}']").locator(
            "mat-checkbox, mat-checkbox-wrapper, label, .mat-mdc-checkbox-inner"
        )
        if await cb.count() > 0 and not await cb.is_checked():
            await cb.first.check()
            print(f"  checked: {sel}")
    except Exception:
        pass
```

**作用：** 自动勾选两个用户协议checkbox，使 `#register_button` 从 disabled 变为 enabled。

---

### 2. `captcha.py`（+73行）—— 新增NoneCap验证码自动破解

#### 2a. 顶部新增NoneCap导入
```python
try:
    from nonecap import NoneCap
except ImportError:
    NoneCap = None
```

#### 2b. 新增 `NoneCapSolver` 类
```python
@dataclass(frozen=True)
class NoneCapSolver:
    api_key: str
    timeout_seconds: int

    async def solve(self, page: Page) -> bool:
        # 1. 检查NoneCap库是否安装
        # 2. 从页面捕获hCaptcha sitekey
        # 3. 调用NoneCap API解题，获取token
        # 4. 将token注入页面（调用__hCaptchaCallback + textarea + captchaResponse事件）
        # 5. 等待#register_button变为enabled
        # 返回True/False
```

#### 2c. `_inject_hcaptcha_token` 增加captchaResponse事件dispatch
```python
// 在原有 __hCaptchaCallback(token) 调用之后，新增：
setTimeout(() => {
    const cd = document.querySelector('[formcontrolname="captcha"]');
    if (cd) {
        cd.dispatchEvent(new CustomEvent('captchaResponse', {
            detail: { response: token, token: token }, bubbles: true,
        }));
    }
}, 500);
// 500ms后再次dispatch，确保Angular表单控件更新
setTimeout(() => { ... }, 2000);
```

**作用：** 作为备份机制，确保Angular的formControlName接收到token变更事件。

#### 2d. `build_captcha_solver` 新增nonecap模式
```python
if config.mode == "nonecap":
    if not config.nonecap_api_key:
        raise ValueError("nonecap_api_key is required")
    return NoneCapSolver(
        api_key=config.nonecap_api_key,
        timeout_seconds=config.timeout_seconds,
    )
```

---

### 3. `config.py`（+12行）—— 新增nonecap配置支持

#### 3a. `CaptchaConfig` dataclass 新增字段
```python
nonecap_api_key: str | None
```

#### 3b. 配置校验新增nonecap模式
```python
if captcha_mode not in {"manual", "yescaptcha", "captcharun", "nonecap"}:
    raise ValueError("captcha.mode must be 'manual', 'yescaptcha', 'captcharun' or 'nonecap'")
nonecap_api_key = _get_str(data, "captcha.nonecap_api_key", "") or None
if captcha_mode == "nonecap" and not nonecap_api_key:
    raise ValueError("captcha.nonecap_api_key is required when captcha.mode = 'nonecap'")
```

#### 3c. 配置模板新增nonecap字段
```toml
nonecap_api_key = ""
```

---

### 4. `config.toml`（配置文件）

```toml
[captcha]
mode = "nonecap"
nonecap_api_key = "nc_live_f5nRC2nA0wyBlkARislXooZx1QzhbAVL"
```

---

### 5. `run_batch.py`（新增文件）

多worker并行批量运行脚本：
- 3个worker同时运行，每个worker独立启动浏览器
- 自动检测CSV已保存数量，不足100个继续跑
- 进程死亡自动重启
- 5分钟+8个key的效率

```python
# 启动方式：
python3 -u run_batch.py
# 或后台运行：
setsid python3 -u run_batch.py > /dev/null 2>&1 &
```

---

## 三、运行效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 注册成功率 | 0%（按钮disabled） | ~95% |
| 单个耗时 | - | 2-3分钟 |
| 并行效率 | - | 3 worker: 5分钟+8个 |
| 总计 | 0 | 48个key |

## 四、API使用

```bash
curl https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer nvapi-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"meta/llama-3.1-8b-instruct","messages":[{"role":"user","content":"hello"}]}'
```
