#!/usr/bin/env python3
"""
测试多个邮箱域名是否被NVIDIA接受
"""

import asyncio
import time
from dataclasses import replace
from playwright.async_api import async_playwright
from config import load_config
from email_providers import DuckMailProvider
from config import DuckMailConfig


async def test_domain(domain: str):
    """测试单个域名"""
    config = load_config()
    duckmail_config = replace(config.duckmail, domain=domain)
    email_provider = DuckMailProvider(duckmail_config)

    test_name = "nv" + str(int(time.time()))[-8:]

    try:
        inbox = email_provider.create_inbox(test_name)
    except Exception as e:
        print(f"  邮箱创建失败: {e}")
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto("https://build.nvidia.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            try:
                btn = page.locator("#onetrust-accept-btn-handler")
                await btn.wait_for(state="visible", timeout=5000)
                await btn.click()
                await asyncio.sleep(1)
            except:
                pass

            login_btn = page.get_by_role("button", name="Login").first
            await login_btn.wait_for(state="visible", timeout=10000)
            await login_btn.click()
            await asyncio.sleep(3)

            email_input = page.locator('input[name="email"]:visible').first
            await email_input.wait_for(state="visible", timeout=10000)
            await email_input.click()
            await email_input.press_sequentially(inbox.address, delay=30)
            await asyncio.sleep(0.3)

            next_btn = page.get_by_role("button", name="Next").filter(visible=True).first
            await next_btn.wait_for(state="visible", timeout=5000)
            await next_btn.click()
            await asyncio.sleep(3)

            current_url = page.url
            if "create-account" in current_url:
                return True
            else:
                return False
        except Exception as e:
            print(f"  错误: {e}")
            return False
        finally:
            await browser.close()


async def test_all_domains():
    domains = [
        "niceground.shop",
        "stoneground.shop",
        "lakeground.shop",
        "canvaspace.shop",
        "vercelspace.shop",
        "seedancespace.shop",
        "happyhorsespace.shop",
        "bananaspace.shop",
        "sunstarmoon.shop",
        "moonstarsun.shop",
        "sunmoonlight.shop",
        "makesomestone.shop",
        "mikesomelike.shop",
        "somestoneair.shop",
        "hubaiclass.org",
        "markaihub.shop",
        "markstonehub.org",
        "glasswhitehub.com",
        "duckmail.sbs",
    ]

    print("测试邮箱域名是否被NVIDIA接受")
    print("=" * 60)

    accepted = []
    rejected = []

    for i, domain in enumerate(domains, 1):
        print(f"\n[{i}/{len(domains)}] 测试: {domain}", end=" ")
        result = await test_domain(domain)
        if result:
            print("✓ 接受")
            accepted.append(domain)
        else:
            print("✗ 拒绝")
            rejected.append(domain)

    print("\n" + "=" * 60)
    print(f"被接受的域名 ({len(accepted)}): {accepted}")
    print(f"被拒绝的域名 ({len(rejected)}): {rejected}")

    return accepted


if __name__ == "__main__":
    accepted = asyncio.run(test_all_domains())
    print(f"\n可用域名数量: {len(accepted)}")
