# NVIDIA Build 批量注册工具

## 功能
自动化注册 NVIDIA Build 账号并批量创建 API Key（`nvapi-...`），支持 hCaptcha 自动破解 + 临时邮箱验证。

## 环境要求
- Python 3.12+
- Ubuntu/Debian（已验证）

## 安装步骤

```bash
# 1. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器（Chromium）
playwright install chromium
```

## 配置说明 (config.toml)

| 字段 | 说明 |
|------|------|
| `email_provider` | 邮箱服务，支持 `duckmail` |
| `duckmail.api_url` | DuckMail API 地址 |
| `duckmail.domain` | 邮箱域名 |
| `captcha.mode` | 验证码服务：`nonecap` / `yescaptcha` / `captcharun` / `manual` |
| `captcha.nonecap_api_key` | NoneCap API Key（REST: `POST /v1/solves`） |
| `nvidia.output_csv` | 输出 CSV 文件路径 |
| `nvidia.account_name` | NVIDIA 组织名 |
| `browser.headless` | 无头模式（true=无界面，服务器推荐） |

## 运行

```bash
# Windows 一键启动图形界面（推荐，双击即可）
start.bat

# 无界面命令行
start_cli.bat 5
python main.py -n 1
```

界面里可以设置数量、点「开始注册」，成功账号会出现在上方列表。

## 输出格式

CSV 文件（默认 `accounts.csv`）：
```
email,password,apikey
nv89183732@duckmail.sbs,vq9PSAAs8KAg,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
```

## API 使用

NVIDIA Build API 端点：`https://integrate.api.nvidia.com/v1`

```bash
curl https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer nvapi-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"meta/llama-3.1-8b-instruct","messages":[{"role":"user","content":"hello"}]}'
```

## 关键文件

| 文件 | 功能 |
|------|------|
| `main.py` | 主逻辑：邮箱注册、hCaptcha、验证码、创建 Key |
| `captcha.py` | hCaptcha 破解器（NoneCap/YesCaptcha/Manual） |
| `email_providers.py` | 临时邮箱服务（DuckMail） |
| `records.py` | CSV 记录保存 |
| `config.py` | 配置解析 |
| `config.toml` | 配置文件 |
| `run_batch.py` | 多 worker 并行批量运行脚本 |

## 已知问题
- `agreeeTermsAndConditions` 和 `chinaPIPLdataGeneralAgreement` 勾选是按钮 enable 的前提（main.py 已处理）
- NoneCap API 有每日额度限制，需在 https://dashboard.nonecap.com 充值
- 后台运行时进程可能被终端断开杀掉，使用 `run_batch.py` 的 `setsid` 模式更稳定
- 浏览器每次注册会创建新实例，消耗约 200MB 内存
