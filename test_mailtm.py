#!/usr/bin/env python3
"""
测试mail.tm域名是否被NVIDIA接受
"""

import asyncio
import time
from playwright.async_api import async_playwright
import requests


def create_mailtm_account():
    """创建mail.tm账户"""
    # 获取可用域名
    domains_response = requests.get("https://api.mail.tm/domains")
    domains = domains_response.json()["hydra:member"]
    
    if not domains:
        return None, None, None
    
    domain = domains[0]["domain"]
    username = f"nv{str(int(time.time()))[-8:]}"
    address = f"{username}@{domain}"
    password = "TestPass123!"
    
    # 创建账户
    account_response = requests.post(
        "https://api.mail.tm/accounts",
        json={
            "address": address,
            "password": password
        }
    )
    
    if account_response.status_code != 201:
        print(f"创建账户失败: {account_response.text}")
        return None, None, None
    
    # 获取token
    token_response = requests.post(
        "https://api.mail.tm/token",
        json={
            "address": address,
            "password": password
        }
    )
    
    if token_response.status_code != 200:
        print(f"获取token失败: {token_response.text}")
        return None, None, None
    
    token = token_response.json()["token"]
    return address, token, domain


async def test_mailtm_domain():
    """测试mail.tm域名"""
    address, token, domain = create_mailtm_account()
    
    if not address:
        print("无法创建mail.tm账户")
        return False
    
    print(f"创建mail.tm邮箱: {address}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            # 打开NVIDIA登录页面
            await page.goto("https://build.nvidia.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            
            # 接受cookie
            try:
                btn = page.locator("#onetrust-accept-btn-handler")
                await btn.wait_for(state="visible", timeout=5000)
                await btn.click()
                await asyncio.sleep(1)
            except:
                pass
            
            # 点击登录按钮
            login_btn = page.get_by_role("button", name="Login").first
            await login_btn.wait_for(state="visible", timeout=10000)
            await login_btn.click()
            await asyncio.sleep(3)
            
            # 输入邮箱
            email_input = page.locator('input[name="email"]:visible').first
            await email_input.wait_for(state="visible", timeout=10000)
            await email_input.click()
            await email_input.press_sequentially(address, delay=30)
            await asyncio.sleep(0.3)
            
            # 点击Next按钮
            next_btn = page.get_by_role("button", name="Next").filter(visible=True).first
            await next_btn.wait_for(state="visible", timeout=5000)
            
            # 等待导航
            async with page.expect_navigation(timeout=15000):
                await next_btn.click()
            
            await asyncio.sleep(3)
            
            # 检查当前URL
            current_url = page.url
            print(f"  当前URL: {current_url}")
            
            if "create-account" in current_url:
                print(f"✓ mail.tm域名 {domain} 被NVIDIA接受")
                return True
            else:
                print(f"✗ mail.tm域名 {domain} 被NVIDIA拒绝")
                return False
                
        except Exception as e:
            print(f"  测试失败: {e}")
            # 获取当前页面内容
            try:
                content = await page.content()
                if "error" in content.lower() or "invalid" in content.lower():
                    print("  页面包含错误信息")
            except:
                pass
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(test_mailtm_domain())
    print(f"\n测试结果: {'通过' if result else '失败'}")
