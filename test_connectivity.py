#!/usr/bin/env python3
"""Smoke-test DuckMail + NoneCap connectivity before running main.py."""

from __future__ import annotations

import secrets
import sys
import time

import requests

from config import load_config
from email_providers import DuckMailProvider, build_email_provider


NONECAP_API = "https://api.nonecap.com/v1"
# Public hCaptcha demo — only used to verify the API key works
DEMO_SITEKEY = "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"
DEMO_URL = "https://accounts.hcaptcha.com/demo"


def test_duckmail() -> bool:
    print("=" * 50)
    print("1) DuckMail")
    print("=" * 50)
    config = load_config()
    provider = build_email_provider(config)
    if not isinstance(provider, DuckMailProvider):
        print(f"  SKIP: email_provider={config.email_provider} (need duckmail)")
        return True

    try:
        domains = provider.list_domains()
        print(f"  domains ({len(domains)}): {', '.join(domains[:8]) or '(none)'}")
    except Exception as exc:
        print(f"  FAIL list domains: {exc}")
        return False

    name = "nv" + secrets.token_hex(4)
    try:
        inbox = provider.create_inbox(name)
        print(f"  OK create inbox: {inbox.address}")
        print(f"  token: {inbox.token[:24]}...")
    except Exception as exc:
        print(f"  FAIL create inbox: {exc}")
        return False

    try:
        resp = requests.get(
            f"{config.duckmail.api_url}/messages",
            headers={"Authorization": f"Bearer {inbox.token}"},
            params={"page": 1},
            timeout=15,
        )
        resp.raise_for_status()
        total = resp.json().get("hydra:totalItems", 0)
        print(f"  OK read inbox (messages={total})")
    except Exception as exc:
        print(f"  FAIL read messages: {exc}")
        return False

    return True


def test_nonecap() -> bool:
    print("\n" + "=" * 50)
    print("2) NoneCap REST (demo sitekey)")
    print("=" * 50)
    config = load_config()
    api_key = config.captcha.nonecap_api_key
    if not api_key:
        print("  FAIL: captcha.nonecap_api_key is empty")
        return False

    print(f"  key: {api_key[:12]}...")
    try:
        response = requests.post(
            f"{NONECAP_API}/solves",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            params={"wait": 30},
            json={
                "type": "hcaptcha",
                "sitekey": DEMO_SITEKEY,
                "url": DEMO_URL,
            },
            timeout=60,
        )
        data = response.json() if response.content else {}
    except Exception as exc:
        print(f"  FAIL request: {exc}")
        return False

    print(f"  HTTP {response.status_code}")
    status = str(data.get("status", ""))
    print(f"  status: {status or data}")

    if response.status_code not in (200, 202):
        print(f"  FAIL: {data}")
        return False

    if status == "solved" and data.get("token"):
        token = data["token"]
        print(f"  OK token: {token[:40]}...")
        print(f"  credits_charged: {data.get('credits_charged')}")
        return True

    solve_id = data.get("id")
    if not solve_id:
        print(f"  FAIL missing id: {data}")
        return False

    print(f"  polling solve {solve_id}...")
    deadline = time.time() + 90
    while time.time() < deadline:
        poll = requests.get(
            f"{NONECAP_API}/solves/{solve_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"wait": 30},
            timeout=45,
        )
        data = poll.json() if poll.content else {}
        status = str(data.get("status", ""))
        print(f"  → {status}")
        if status == "solved" and data.get("token"):
            print(f"  OK token: {data['token'][:40]}...")
            return True
        if status in {"failed", "cancelled", "expired"}:
            print(f"  FAIL: {data.get('error') or data}")
            return False
        time.sleep(2)

    print("  FAIL timeout")
    return False


def main() -> int:
    ok_mail = test_duckmail()
    ok_cap = test_nonecap()
    print("\n" + "=" * 50)
    print(f"DuckMail: {'PASS' if ok_mail else 'FAIL'}")
    print(f"NoneCap:  {'PASS' if ok_cap else 'FAIL'}")
    print("=" * 50)
    if ok_mail and ok_cap:
        print("\nReady. Run: python main.py -n 1")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
