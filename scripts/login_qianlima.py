"""命令行建立千里马登录态。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path

# 加载 .env 文件，让 os.getenv 能读到 QIANLIMA_PASSWORD 等变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.templates.qianlima_login import (
    DEFAULT_LOGIN_URL,
    login_and_save_cookies,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="打开千里马登录页并保存 Playwright storage_state"
    )
    parser.add_argument(
        "--username",
        default=os.getenv("QIANLIMA_USERNAME", ""),
        help="千里马账号；缺省时交互输入",
    )
    parser.add_argument(
        "--login-url",
        default=os.getenv(
            "QIANLIMA_LOGIN_URL",
            DEFAULT_LOGIN_URL,
        ),
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=None,
        help="可选 storage_state 文件路径",
    )
    parser.add_argument(
        "--dom-config",
        type=Path,
        default=Path("qianlima-dom.json"),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="等待用户完成验证码的秒数",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()

    username = args.username.strip()
    if not username:
        username = input("千里马账号: ").strip()

    password = os.getenv("QIANLIMA_PASSWORD", "")
    if not password:
        password = getpass.getpass("千里马密码: ")

    result = await login_and_save_cookies(
        username=username,
        password=password,
        login_url=args.login_url,
        cookie_file=args.session_file,
        wait_timeout_seconds=args.timeout,
        dom_config_path=args.dom_config,
    )

    if result["success"]:
        print(
            "登录态保存成功:",
            result["session_path"],
        )
        return 0

    print("登录失败:", result["error"])
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
