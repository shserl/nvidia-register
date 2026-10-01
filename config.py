from __future__ import annotations

import sys
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.toml"


def save_nonecap_api_key(api_key: str) -> None:
    """Update captcha.nonecap_api_key in config.toml (create line if missing)."""
    key = api_key.strip()
    if not key:
        raise ValueError("API Key 不能为空")
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing config file: {CONFIG_FILE}")

    text = CONFIG_FILE.read_text(encoding="utf-8")
    pattern = re.compile(r'(?m)^(nonecap_api_key\s*=\s*)".*?"\s*$')
    if pattern.search(text):
        text = pattern.sub(rf'\1"{key}"', text, count=1)
    else:
        # insert under [captcha] if present
        captcha = re.search(r"(?m)^\[captcha\]\s*$", text)
        if captcha:
            insert_at = captcha.end()
            text = text[:insert_at] + f'\nnonecap_api_key = "{key}"' + text[insert_at:]
        else:
            text += f'\n\n[captcha]\nnonecap_api_key = "{key}"\n'
    CONFIG_FILE.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class CloudflareTempEmailConfig:
    api_url: str
    admin_auth: str
    domain: str


@dataclass(frozen=True)
class DuckMailConfig:
    api_url: str
    domain: str
    api_key: str | None


@dataclass(frozen=True)
class MailTmConfig:
    api_url: str
    domain: str  # empty = pick first active domain from API


@dataclass(frozen=True)
class CaptchaConfig:
    mode: str
    yescaptcha_client_key: str | None
    yescaptcha_api_url: str
    captcharun_token: str | None
    captcharun_api_url: str
    nonecap_api_key: str | None
    poll_interval_seconds: int
    timeout_seconds: int


@dataclass(frozen=True)
class NvidiaConfig:
    output_csv: Path
    key_name: str
    account_name: str
    key_expiry_date: str


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool
    close_delay_seconds: int
    channel: str  # "" | "msedge" | "chrome"


@dataclass(frozen=True)
class AppConfig:
    email_provider: str
    cloudflare_temp_email: CloudflareTempEmailConfig
    duckmail: DuckMailConfig
    mail_tm: MailTmConfig
    captcha: CaptchaConfig
    nvidia: NvidiaConfig
    browser: BrowserConfig


def save_email_provider(provider: str) -> None:
    """Update top-level email_provider in config.toml."""
    provider = provider.strip().lower()
    if provider in {"mailtm", "mail_tm"}:
        provider = "mail.tm"
    if provider not in {"cloudflare_temp_email", "duckmail", "mail.tm"}:
        raise ValueError("email_provider must be duckmail / mail.tm / cloudflare_temp_email")
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing config file: {CONFIG_FILE}")
    text = CONFIG_FILE.read_text(encoding="utf-8")
    pattern = re.compile(r'(?m)^(email_provider\s*=\s*)".*?"\s*$')
    if pattern.search(text):
        text = pattern.sub(rf'\1"{provider}"', text, count=1)
    else:
        text = f'email_provider = "{provider}"\n' + text
    CONFIG_FILE.write_text(text, encoding="utf-8")


def _require_str(data: dict[str, Any], path: str) -> str:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Missing required config: {path}")
        current = current[part]
    if not isinstance(current, str) or not current.strip():
        raise ValueError(f"Missing required config: {path}")
    return current.strip()


def _get_str(data: dict[str, Any], path: str, default: str) -> str:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current if isinstance(current, str) and current.strip() else default


def _get_int(data: dict[str, Any], path: str, default: int) -> int:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current if isinstance(current, int) else default


def _get_bool(data: dict[str, Any], path: str, default: bool) -> bool:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current if isinstance(current, bool) else default


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR / path


def init_config() -> None:
    if CONFIG_FILE.exists():
        print(f"Config already exists: {CONFIG_FILE}")
        return
    template = """
email_provider = "duckmail"

[cloudflare_temp_email]
api_url = ""
admin_auth = ""
domain = ""

[duckmail]
api_url = "https://api.duckmail.sbs"
domain = "duckmail.sbs"
api_key = ""

[mail_tm]
api_url = "https://api.mail.tm"
domain = ""  # 留空则自动选用 Mail.tm 当前可用域名

[captcha]
mode = "nonecap" # manual | yescaptcha | captcharun | nonecap
yescaptcha_client_key = ""
yescaptcha_api_url = "https://api.yescaptcha.com"
captcharun_token = ""
captcharun_api_url = "https://api.captcha-run.com"
nonecap_api_key = ""
poll_interval_seconds = 3
timeout_seconds = 180

[nvidia]
output_csv = "accounts.csv"
key_name = "api"
account_name = "NVIDIA Build"
key_expiry_date = "2126-05-08T08:00:00Z"

[browser]
headless = false
close_delay_seconds = 5
channel = "msedge"  # msedge | chrome | 空=内置 Chromium
"""
    CONFIG_FILE.write_text(template, encoding="utf-8")
    print(f"Created {CONFIG_FILE}")


def load_config() -> AppConfig:
    if not CONFIG_FILE.exists():
        print(f"Missing config file: {CONFIG_FILE}")
        print("Run: python main.py --init")
        sys.exit(1)

    with CONFIG_FILE.open("rb") as file:
        data = tomllib.load(file)

    email_provider = _get_str(data, "email_provider", "cloudflare_temp_email").lower()
    if email_provider in {"mailtm", "mail_tm"}:
        email_provider = "mail.tm"
    if email_provider not in {"cloudflare_temp_email", "duckmail", "mail.tm"}:
        raise ValueError(f"Unsupported email_provider: {email_provider}")

    use_cloudflare_temp_email = email_provider == "cloudflare_temp_email"
    use_duckmail = email_provider == "duckmail"

    cloudflare_api_url = (
        _require_str(data, "cloudflare_temp_email.api_url")
        if use_cloudflare_temp_email
        else _get_str(data, "cloudflare_temp_email.api_url", "")
    ).rstrip("/")
    cloudflare_admin_auth = (
        _require_str(data, "cloudflare_temp_email.admin_auth")
        if use_cloudflare_temp_email
        else _get_str(data, "cloudflare_temp_email.admin_auth", "")
    )
    cloudflare_domain = (
        _require_str(data, "cloudflare_temp_email.domain")
        if use_cloudflare_temp_email
        else _get_str(data, "cloudflare_temp_email.domain", "")
    )

    duckmail_domain = (
        _require_str(data, "duckmail.domain")
        if use_duckmail
        else _get_str(data, "duckmail.domain", "")
    )
    duckmail_api_key = _get_str(data, "duckmail.api_key", "") or None
    mail_tm_domain = _get_str(data, "mail_tm.domain", "")

    captcha_mode = _get_str(data, "captcha.mode", "manual").lower()
    if captcha_mode not in {"manual", "yescaptcha", "captcharun", "nonecap"}:
        raise ValueError("captcha.mode must be 'manual', 'yescaptcha', 'captcharun' or 'nonecap'")
    yescaptcha_client_key = _get_str(data, "captcha.yescaptcha_client_key", "") or None
    if captcha_mode == "yescaptcha" and not yescaptcha_client_key:
        raise ValueError("captcha.yescaptcha_client_key is required when captcha.mode = 'yescaptcha'")
    captcharun_token = _get_str(data, "captcha.captcharun_token", "") or None
    if captcha_mode == "captcharun" and not captcharun_token:
        raise ValueError("captcha.captcharun_token is required when captcha.mode = 'captcharun'")
    nonecap_api_key = _get_str(data, "captcha.nonecap_api_key", "") or None
    if captcha_mode == "nonecap" and not nonecap_api_key:
        raise ValueError("captcha.nonecap_api_key is required when captcha.mode = 'nonecap'")

    return AppConfig(
        email_provider=email_provider,
        cloudflare_temp_email=CloudflareTempEmailConfig(
            api_url=cloudflare_api_url,
            admin_auth=cloudflare_admin_auth,
            domain=cloudflare_domain,
        ),
        duckmail=DuckMailConfig(
            api_url=_get_str(data, "duckmail.api_url", "https://api.duckmail.sbs").rstrip("/"),
            domain=duckmail_domain,
            api_key=duckmail_api_key,
        ),
        mail_tm=MailTmConfig(
            api_url=_get_str(data, "mail_tm.api_url", "https://api.mail.tm").rstrip("/"),
            domain=mail_tm_domain,
        ),
        captcha=CaptchaConfig(
            mode=captcha_mode,
            yescaptcha_client_key=yescaptcha_client_key,
            yescaptcha_api_url=_get_str(data, "captcha.yescaptcha_api_url", "https://api.yescaptcha.com").rstrip("/"),
            captcharun_token=captcharun_token,
            captcharun_api_url=_get_str(data, "captcha.captcharun_api_url", "https://api.captcha-run.com").rstrip("/"),
            nonecap_api_key=nonecap_api_key,
            poll_interval_seconds=_get_int(data, "captcha.poll_interval_seconds", 3),
            timeout_seconds=_get_int(data, "captcha.timeout_seconds", 180),
        ),
        nvidia=NvidiaConfig(
            output_csv=_resolve_path(_get_str(data, "nvidia.output_csv", "accounts.csv")),
            key_name=_get_str(data, "nvidia.key_name", "api"),
            account_name=_get_str(data, "nvidia.account_name", "NVIDIA Build"),
            key_expiry_date=_get_str(data, "nvidia.key_expiry_date", "2126-05-08T08:00:00Z"),
        ),
        browser=BrowserConfig(
            headless=_get_bool(data, "browser.headless", True),
            close_delay_seconds=_get_int(data, "browser.close_delay_seconds", 2),
            channel=_get_str(data, "browser.channel", "").strip().lower(),
        ),
    )


def describe_config(config: AppConfig) -> None:
    if config.email_provider == "cloudflare_temp_email":
        email_api = config.cloudflare_temp_email.api_url
        email_domain = config.cloudflare_temp_email.domain
    elif config.email_provider == "mail.tm":
        email_api = config.mail_tm.api_url
        email_domain = config.mail_tm.domain or "(auto)"
    else:
        email_api = config.duckmail.api_url
        email_domain = config.duckmail.domain

    print(f"  EMAIL_PROVIDER: {config.email_provider}")
    print(f"  EMAIL_API:      {email_api}")
    print(f"  EMAIL_DOMAIN:   {email_domain}")
    print(f"  CAPTCHA_MODE:   {config.captcha.mode}")
    print(f"  BROWSER:        channel={config.browser.channel or 'chromium'} headless={config.browser.headless}")
    print(f"  OUTPUT_CSV:     {config.nvidia.output_csv}")
    print(f"  CONFIG_FILE:    {CONFIG_FILE}")
