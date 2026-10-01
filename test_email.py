#!/usr/bin/env python3
"""
测试邮箱服务是否可用
"""

import sys
import time
from config import load_config
from email_providers import build_email_provider

def test_email_provider():
    config = load_config()
    email_provider = build_email_provider(config)
    
    print(f"测试邮箱服务: {config.email_provider}")
    print(f"邮箱域名: {config.duckmail.domain if config.email_provider == 'duckmail' else config.cloudflare_temp_email.domain}")
    
    # 创建测试邮箱
    test_name = "test_" + str(int(time.time()))[-8:]
    print(f"\n创建测试邮箱: {test_name}")
    
    try:
        inbox = email_provider.create_inbox(test_name)
        print(f"✓ 邮箱创建成功: {inbox.address}")
        print(f"  Token: {inbox.token[:20]}...")
        return True
    except Exception as e:
        print(f"✗ 邮箱创建失败: {e}")
        return False

if __name__ == "__main__":
    success = test_email_provider()
    sys.exit(0 if success else 1)