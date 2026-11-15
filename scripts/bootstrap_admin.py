#!/usr/bin/env python3
import getpass
import sys

import httpx

print("首次创建家庭；不会写入固定口令。服务须已启动。")
username = input("用户名: ").strip()
display_name = input("你的称呼: ").strip()
household_name = input("家庭名称: ").strip()
password = getpass.getpass("至少 12 位口令: ")
if password != getpass.getpass("再次输入口令: "):
    sys.exit("两次口令不一致")
with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False) as client:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": username,
            "display_name": display_name,
            "household_name": household_name,
            "password": password,
        },
    )
    if response.status_code != 200:
        sys.exit(response.json().get("message", "初始化失败"))
print("家庭已创建，请在浏览器登录。")
