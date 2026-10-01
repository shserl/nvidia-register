#!/usr/bin/env python3
"""
测试各种邮箱域名是否被NVIDIA接受
包括免费邮箱和临时邮箱
"""

import asyncio
import time
import random
import string
from playwright.async_api import async_playwright
import requests


def generate_random_email():
    """生成随机邮箱地址"""
    # 使用常见的免费邮箱域名
    free_domains = [
        "gmail.com",
        "outlook.com", 
        "yahoo.com",
        "hotmail.com",
        "live.com",
        "protonmail.com",
        "proton.me",
        "tutanota.com",
        "zoho.com",
        "yandex.com",
        "mail.com",
        "email.com",
        "gmx.com",
        "icloud.com"
    ]
    
    # 生成随机用户名
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    domain = random.choice(free_domains)
    
    return f"{username}@{domain}", domain


async def test_free_email():
    """测试免费邮箱域名"""
    email, domain = generate_random_email()
    print(f"\n测试免费邮箱域名: {domain}")
    print(f"生成的邮箱: {email}")
    
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
            await email_input.press_sequentially(email, delay=30)
            await asyncio.sleep(0.3)
            
            # 点击Next按钮
            next_btn = page.get_by_role("button", name="Next").filter(visible=True).first
            await next_btn.wait_for(state="visible", timeout=5000)
            
            # 等待导航
            try:
                async with page.expect_navigation(timeout=15000):
                    await next_btn.click()
            except:
                pass
            
            await asyncio.sleep(3)
            
            # 检查当前URL
            current_url = page.url
            print(f"  当前URL: {current_url}")
            
            if "create-account" in current_url:
                print(f"✓ {domain} 被NVIDIA接受")
                return True, domain
            else:
                print(f"✗ {domain} 被NVIDIA拒绝")
                return False, domain
                
        except Exception as e:
            print(f"  测试失败: {e}")
            return False, domain
        finally:
            await browser.close()


async def test_all_free_domains():
    """测试所有免费邮箱域名"""
    domains = [
        "gmail.com",
        "outlook.com", 
        "yahoo.com",
        "hotmail.com",
        "live.com",
        "protonmail.com",
        "proton.me",
        "tutanota.com",
        "zoho.com",
        "yandex.com",
        "mail.com",
        "email.com",
        "gmx.com",
        "icloud.com"
    ]
    
    print("测试免费邮箱域名是否被NVIDIA接受")
    print("=" * 60)
    
    accepted = []
    rejected = []
    
    for i, domain in enumerate(domains, 1):
        print(f"\n[{i}/{len(domains)}] 测试: {domain}")
        
        # 生成随机邮箱
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email = f"{username}@{domain}"
        
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
                await email_input.press_sequentially(email, delay=30)
                await asyncio.sleep(0.3)
                
                # 点击Next按钮
                next_btn = page.get_by_role("button", name="Next").filter(visible=True).first
                await next_btn.wait_for(state="visible", timeout=5000)
                
                # 等待导航
                try:
                    async with page.expect_navigation(timeout=15000):
                        await next_btn.click()
                except:
                    pass
                
                await asyncio.sleep(3)
                
                # 检查当前URL
                current_url = page.url
                
                if "create-account" in current_url:
                    print(f"  ✓ {domain} 接受")
                    accepted.append(domain)
                else:
                    print(f"  ✗ {domain} 拒绝")
                    rejected.append(domain)
                    
            except Exception as e:
                print(f"  错误: {e}")
                rejected.append(domain)
            finally:
                await browser.close()
    
    print("\n" + "=" * 60)
    print(f"被接受的域名 ({len(accepted)}): {accepted}")
    print(f"被拒绝的域名 ({len(rejected)}): {rejected}")
    
    return accepted


if __name__ == "__main__":
    accepted = asyncio.run(test_all_free_domains())
    print(f"\n可用域名数量: {len(accepted)}")
