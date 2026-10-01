from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from config import AppConfig, CloudflareTempEmailConfig, DuckMailConfig, MailTmConfig


@dataclass(frozen=True)
class TempEmailInbox:
    address: str
    token: str


class TempEmailProvider(Protocol):
    def create_inbox(self, name: str) -> TempEmailInbox:
        ...

    def poll_verification_code(
        self,
        inbox: TempEmailInbox,
        timeout_seconds: int = 180,
        exclude_codes: set[str] | frozenset[str] | None = None,
    ) -> str | None:
        ...


class CloudflareTempEmailProvider:
    def __init__(self, config: CloudflareTempEmailConfig):
        self.config = config

    def create_inbox(self, name: str) -> TempEmailInbox:
        response = requests.post(
            f"{self.config.api_url}/admin/new_address",
            headers={"x-admin-auth": self.config.admin_auth, "Content-Type": "application/json"},
            json={"name": name, "domain": self.config.domain, "enablePrefix": False},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        address = data.get("address", "")
        token = data.get("jwt", "")
        if not address or not token:
            raise RuntimeError(f"Email creation failed: {data}")
        return TempEmailInbox(address=address, token=token)

    def poll_verification_code(
        self,
        inbox: TempEmailInbox,
        timeout_seconds: int = 180,
        exclude_codes: set[str] | frozenset[str] | None = None,
    ) -> str | None:
        skip = {c.strip() for c in (exclude_codes or set()) if c}
        deadline = time.time() + timeout_seconds
        headers = {"Authorization": f"Bearer {inbox.token}"}
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{self.config.api_url}/api/mails?limit=5&offset=0",
                    headers=headers,
                    timeout=15,
                )
                data = response.json()
                mails = data.get("results") or data.get("data") or []
                for mail in mails:
                    mail_id = mail.get("id") or mail.get("_id")
                    if not mail_id:
                        continue
                    detail_response = requests.get(
                        f"{self.config.api_url}/api/mail/{mail_id}",
                        headers=headers,
                        timeout=15,
                    )
                    code = _extract_verification_code(detail_response.json().get("raw", ""))
                    if code and code not in skip:
                        return code
            except Exception as exc:
                print(f"  email poll: {exc}", flush=True)
            time.sleep(2)
        return None


# NVIDIA 会拒收的临时邮域名（Continue 无法启用）
_BLOCKED_EMAIL_DOMAINS = {
    "duckmail.sbs",
    "mail.tm",
    "guerrillamail.com",
    "tempmail.com",
}


class DuckMailProvider:
    """Temporary inbox via DuckMail API (https://api.duckmail.sbs).

    Flow: GET /domains → POST /accounts → POST /token → GET /messages
    Docs: https://raw.githubusercontent.com/MoonWeSif/DuckMail/main/public/llm-api-docs.txt
    """

    def __init__(self, config: DuckMailConfig):
        self.config = config
        self._domains_cache: list[str] | None = None

    def create_inbox(self, name: str) -> TempEmailInbox:
        if len(name) < 3:
            raise ValueError("DuckMail username must be >= 3 characters")

        domain = self._resolve_domain()
        if domain.lower() in _BLOCKED_EMAIL_DOMAINS:
            raise RuntimeError(
                f"Domain {domain} is blocked by NVIDIA sign-in. "
                f"Set duckmail.domain to a shop domain from GET /domains (e.g. niceground.shop)."
            )

        address = f"{name}@{domain}"
        password = f"dm_{secrets.token_hex(8)}"

        # expiresIn: 0 = never expires (keep inbox until registration finishes)
        response = requests.post(
            f"{self.config.api_url}/accounts",
            headers=self._account_headers(),
            json={"address": address, "password": password, "expiresIn": 0},
            timeout=20,
        )
        if response.status_code == 409:
            # Address collision — retry with a random suffix
            address = f"{name}{secrets.token_hex(2)}@{domain}"
            response = requests.post(
                f"{self.config.api_url}/accounts",
                headers=self._account_headers(),
                json={"address": address, "password": password, "expiresIn": 0},
                timeout=20,
            )
        if not response.ok:
            raise RuntimeError(
                f"DuckMail create account failed ({response.status_code}): {response.text[:300]}"
            )

        token_response = requests.post(
            f"{self.config.api_url}/token",
            headers={"Content-Type": "application/json"},
            json={"address": address, "password": password},
            timeout=20,
        )
        if not token_response.ok:
            raise RuntimeError(
                f"DuckMail token failed ({token_response.status_code}): {token_response.text[:300]}"
            )
        data = token_response.json()
        token = data.get("token", "")
        if not token:
            raise RuntimeError(f"DuckMail token acquisition failed: {data}")
        print(f"  DuckMail inbox ready: {address}")
        return TempEmailInbox(address=address, token=token)

    def poll_verification_code(
        self,
        inbox: TempEmailInbox,
        timeout_seconds: int = 180,
        exclude_codes: set[str] | frozenset[str] | None = None,
    ) -> str | None:
        skip = {c.strip() for c in (exclude_codes or set()) if c}
        deadline = time.time() + timeout_seconds
        headers = {"Authorization": f"Bearer {inbox.token}"}
        seen_ids: set[str] = set()
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{self.config.api_url}/messages",
                    headers=headers,
                    params={"page": 1},
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                messages = data.get("hydra:member") or []
                for message in messages:
                    message_id = str(message.get("id") or "")
                    if not message_id or message_id in seen_ids:
                        continue
                    detail_response = requests.get(
                        f"{self.config.api_url}/messages/{message_id}",
                        headers=headers,
                        timeout=15,
                    )
                    detail_response.raise_for_status()
                    detail = detail_response.json()
                    body = _duckmail_message_body(detail)
                    subject = str(detail.get("subject") or "")
                    code = _extract_verification_code(f"{subject}\n{body}")
                    if code and code not in skip:
                        print(f"  DuckMail code from: {subject[:60] or '(no subject)'}")
                        return code
                    seen_ids.add(message_id)
            except Exception as exc:
                print(f"  email poll: {exc}", flush=True)
            time.sleep(2)
        return None

    def list_domains(self) -> list[str]:
        """GET /domains — verified system/private domains."""
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        response = requests.get(
            f"{self.config.api_url}/domains",
            headers=headers,
            params={"page": 1},
            timeout=15,
        )
        response.raise_for_status()
        members = response.json().get("hydra:member") or []
        domains = [
            str(item["domain"])
            for item in members
            if isinstance(item, dict) and item.get("domain") and item.get("isVerified", True)
        ]
        return domains

    def _resolve_domain(self) -> str:
        configured = (self.config.domain or "").strip().lower()
        if configured and configured not in _BLOCKED_EMAIL_DOMAINS:
            return configured

        if self._domains_cache is None:
            self._domains_cache = self.list_domains()

        usable = [d for d in self._domains_cache if d.lower() not in _BLOCKED_EMAIL_DOMAINS]
        if not usable:
            raise RuntimeError(
                "DuckMail: no usable domains (NVIDIA blocks duckmail.sbs). "
                "Check GET /domains or set duckmail.domain manually."
            )
        if configured in _BLOCKED_EMAIL_DOMAINS:
            print(f"  duckmail.domain={configured} blocked by NVIDIA, using {usable[0]}")
        return usable[0]

    def _account_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


class MailTmProvider:
    """Temporary inbox via Mail.tm API (https://docs.mail.tm/).

    Flow: GET /domains → POST /accounts → POST /token → GET /messages
    Note: the literal domain mail.tm is often blocked; use API-provided domains.
    """

    def __init__(self, config: MailTmConfig):
        self.config = config
        self._domains_cache: list[str] | None = None

    def create_inbox(self, name: str) -> TempEmailInbox:
        if len(name) < 3:
            raise ValueError("Mail.tm username must be >= 3 characters")

        domain = self._resolve_domain()
        if domain.lower() in _BLOCKED_EMAIL_DOMAINS:
            raise RuntimeError(
                f"Domain {domain} is blocked by NVIDIA sign-in. "
                f"Leave mail_tm.domain empty or set another domain from GET /domains."
            )

        address = f"{name}@{domain}"
        password = f"mt_{secrets.token_hex(8)}"

        response = requests.post(
            f"{self.config.api_url}/accounts",
            headers={"Content-Type": "application/json"},
            json={"address": address, "password": password},
            timeout=20,
        )
        if response.status_code == 409:
            address = f"{name}{secrets.token_hex(2)}@{domain}"
            response = requests.post(
                f"{self.config.api_url}/accounts",
                headers={"Content-Type": "application/json"},
                json={"address": address, "password": password},
                timeout=20,
            )
        if not response.ok:
            raise RuntimeError(
                f"Mail.tm create account failed ({response.status_code}): {response.text[:300]}"
            )

        token_response = requests.post(
            f"{self.config.api_url}/token",
            headers={"Content-Type": "application/json"},
            json={"address": address, "password": password},
            timeout=20,
        )
        if not token_response.ok:
            raise RuntimeError(
                f"Mail.tm token failed ({token_response.status_code}): {token_response.text[:300]}"
            )
        data = token_response.json()
        token = data.get("token", "")
        if not token:
            raise RuntimeError(f"Mail.tm token acquisition failed: {data}")
        print(f"  Mail.tm inbox ready: {address}")
        return TempEmailInbox(address=address, token=token)

    def poll_verification_code(
        self,
        inbox: TempEmailInbox,
        timeout_seconds: int = 180,
        exclude_codes: set[str] | frozenset[str] | None = None,
    ) -> str | None:
        skip = {c.strip() for c in (exclude_codes or set()) if c}
        deadline = time.time() + timeout_seconds
        headers = {
            "Authorization": f"Bearer {inbox.token}",
            "Accept": "application/ld+json, application/json",
        }
        seen_ids: set[str] = set()
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{self.config.api_url}/messages",
                    headers=headers,
                    params={"page": 1},
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    messages = data
                else:
                    messages = data.get("hydra:member") or data.get("member") or []
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    message_id = str(message.get("id") or "")
                    if not message_id or message_id in seen_ids:
                        continue
                    detail_response = requests.get(
                        f"{self.config.api_url}/messages/{message_id}",
                        headers=headers,
                        timeout=15,
                    )
                    detail_response.raise_for_status()
                    detail = detail_response.json()
                    body = _duckmail_message_body(detail)
                    subject = str(detail.get("subject") or "")
                    intro = str(message.get("intro") or "")
                    code = _extract_verification_code(f"{subject}\n{intro}\n{body}")
                    if code and code not in skip:
                        print(f"  Mail.tm code from: {subject[:60] or '(no subject)'}")
                        return code
                    seen_ids.add(message_id)
            except Exception as exc:
                print(f"  email poll: {exc}", flush=True)
            time.sleep(2)
        return None

    def list_domains(self) -> list[str]:
        response = requests.get(
            f"{self.config.api_url}/domains",
            headers={"Accept": "application/ld+json, application/json"},
            params={"page": 1},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            members = data
        else:
            members = data.get("hydra:member") or data.get("member") or []
        domains: list[str] = []
        for item in members:
            if not isinstance(item, dict):
                continue
            if item.get("isActive") is False:
                continue
            d = item.get("domain")
            if d:
                domains.append(str(d))
        return domains

    def _resolve_domain(self) -> str:
        configured = (self.config.domain or "").strip().lower()
        if configured and configured not in _BLOCKED_EMAIL_DOMAINS:
            return configured

        if self._domains_cache is None:
            self._domains_cache = self.list_domains()

        usable = [d for d in self._domains_cache if d.lower() not in _BLOCKED_EMAIL_DOMAINS]
        if not usable:
            raise RuntimeError(
                "Mail.tm: no usable domains (NVIDIA may block mail.tm). "
                "Check GET https://api.mail.tm/domains"
            )
        if configured in _BLOCKED_EMAIL_DOMAINS:
            print(f"  mail_tm.domain={configured} blocked by NVIDIA, using {usable[0]}")
        return usable[0]


def _extract_verification_code(raw_message: str) -> str | None:
    clean = re.sub(r"=\r?\n", "", raw_message)
    index = clean.lower().find("verification code")
    if index >= 0:
        snippet = clean[index : index + 500]
        match = re.search(r"(\d{3})\s*[-–]\s*(\d{3})", snippet)
        if match:
            return match.group(1) + match.group(2)
    match = re.search(r"(?<!\d)(\d{3})[-–](\d{3})(?!\d)", clean)
    if match:
        return match.group(1) + match.group(2)
    return None


def _duckmail_message_body(detail: dict) -> str:
    parts: list[str] = []
    text = detail.get("text")
    if isinstance(text, str) and text.strip():
        parts.append(text)

    html = detail.get("html") or []
    if isinstance(html, list):
        for item in html:
            if isinstance(item, str) and item.strip():
                parts.append(item)
    elif isinstance(html, str) and html.strip():
        parts.append(html)

    return "\n".join(parts)


def build_email_provider(config: AppConfig) -> TempEmailProvider:
    if config.email_provider == "cloudflare_temp_email":
        return CloudflareTempEmailProvider(config.cloudflare_temp_email)
    if config.email_provider == "duckmail":
        return DuckMailProvider(config.duckmail)
    if config.email_provider == "mail.tm":
        return MailTmProvider(config.mail_tm)
    raise ValueError(f"Unsupported email provider: {config.email_provider}")
