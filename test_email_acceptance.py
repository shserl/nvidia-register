#!/usr/bin/env python3
"""
快速测试邮箱是否被NVIDIA支持
"""

import asyncio
import time
from playwright.async_api import async_playwright
from config import load_config
from email_providers import build_email_provider

async def test_email_acceptance():
    config = load_config()
    email_provider = build_email_provider(config)
    
    # 创建测试邮箱
    test_name = "test_" + str(int(time.time()))[-8:]
    print(f"创建测试邮箱: {test_name}")
    
    try:
        inbox = email_provider.create_inbox(test_name)
        print(f"✓ 邮箱创建成功: {inbox.address}")
    except Exception as e:
        print(f"✗ 邮箱创建失败: {e}")
        return False
    
    # 启动浏览器测试
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            print(f"\n测试邮箱 {inbox.address} 是否被NVIDIA接受...")
            
            # 打开NVIDIA登录页面
            await page.goto("https://build.nvidia.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            
            # 点击登录按钮
            try:
                login_btn = page.get_by_role("button", name="Login").first
                await login_btn.wait_for(state="visible", timeout=10000)
                await login_btn.click()
                await asyncio.sleep(3)
            except Exception as e:
                print(f"  点击登录按钮失败: {e}")
                return False
            
            # 输入邮箱
            try:
                email_input = page.locator('input[name="email"]:visible').first
                await email_input.wait_for(state="visible", timeout=10000)
                await email_input.click()
                await email_input.press_sequentially(inbox.address, delay=50)
                await asyncio.sleep(0.5)
                
                # 点击Next按钮
                next_btn = page.get_by_role("button", name="Next").filter(visible=True).first
                await next_btn.wait_for(state="visible", timeout=5000)
                await next_btn.click()
                await asyncio.sleep(3)
                
                # 检查是否跳转到注册页面
                current_url = page.url
                if "create-account" in current_url:
                    print(f"✓ 邮箱 {inbox.address} 被NVIDIA接受")
                    return True
                else:
                    print(f"✗ 邮箱 {inbox.address} 被NVIDIA拒绝")
                    print(f"  当前URL: {current_url}")
                    return False
                    
            except Exception as e:
                print(f"  测试失败: {e}")
                return False
                
        finally:
            await browser.close()

if __name__ == "__main__":
    result = asyncio.run(test_email_acceptance())
    print(f"\n测试结果: {'通过' if result else '失败'}")
