#!/usr/bin/env python3
"""标小智一键启动脚本（评委友好版）。

功能：
1. 检查 Python 版本 >= 3.11
2. 检查关键依赖是否已安装（缺失时自动 pip install）
3. 检查 .env 配置文件（缺失时从 .env.example 复制并提示填密钥）
4. 检查数据库是否存在（缺失时初始化）
5. 检查 examples/ 示例文件可加载
6. 启动 uvicorn 服务并打开浏览器

使用：
    python run_demo.py

注意：本脚本不覆盖已有 .env / 数据库，只做缺失项补全。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(str(ROOT))

# 关键依赖（包名 -> import 名）
REQUIRED_DEPS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "aiosqlite": "aiosqlite",
    "pydantic": "pydantic",
    "httpx": "httpx",
    "playwright": "playwright",
    "slowapi": "slowapi",
    "loguru": "loguru",
    "jieba": "jieba",
    "argon2": "argon2",
    "cryptography": "cryptography",
}


def step(name: str) -> None:
    print(f"\n{'=' * 50}\n[{name}]\n{'=' * 50}")


def check_python() -> None:
    """检查 Python 版本 >= 3.11。"""
    step("1/6 检查 Python 版本")
    if sys.version_info < (3, 11):
        print(f"[FAIL] Python {sys.version_info.major}.{sys.version_info.minor} 版本过低，需要 3.11+")
        print("       请升级 Python: https://www.python.org/downloads/")
        sys.exit(1)
    print(f"[OK] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


def check_deps() -> None:
    """检查关键依赖，缺失时自动安装。"""
    step("2/6 检查依赖")
    missing = []
    for pkg, imp in REQUIRED_DEPS.items():
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"[WARN] 缺失依赖: {', '.join(missing)}")
        print("       正在自动安装 (pip install -r requirements.txt)...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                check=True,
                cwd=str(ROOT),
            )
            print("[OK] 依赖安装完成")
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] 依赖安装失败: {e}")
            print("       请手动执行: pip install -r requirements.txt")
            sys.exit(1)
    else:
        print(f"[OK] 关键依赖已安装 ({len(REQUIRED_DEPS)} 个)")


def check_env() -> None:
    """检查 .env 文件，缺失时从 .env.example 复制。"""
    step("3/6 检查环境变量配置")
    env_file = ROOT / ".env"
    example_file = ROOT / ".env.example"

    if not env_file.exists():
        if example_file.exists():
            print("[WARN] .env 不存在，从 .env.example 复制...")
            env_file.write_text(example_file.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[OK] 已创建 .env: {env_file}")
        else:
            print("[FAIL] .env 和 .env.example 都不存在")
            sys.exit(1)
    else:
        print(f"[OK] .env 已存在: {env_file}")

    # 检查 SECRET_KEY 是否还是占位符
    content = env_file.read_text(encoding="utf-8")
    if "0000000000000000000000000000000000000000000000000000000000000000" in content:
        print("[WARN] SECRET_KEY 仍是占位符，正在生成随机密钥...")
        try:
            import secrets
            key = secrets.token_hex(32)
            content = content.replace(
                "0000000000000000000000000000000000000000000000000000000000000000",
                key,
            )
            env_file.write_text(content, encoding="utf-8")
            print(f"[OK] SECRET_KEY 已生成 ({key[:16]}...)")
        except Exception as e:
            print(f"[WARN] SECRET_KEY 生成失败: {e}，请手动执行:")
            print('       python -c "import secrets; print(secrets.token_hex(32))"')
    else:
        print("[OK] SECRET_KEY 已配置")

    # 检查 LLM API key（仅提示，不强制）
    if "DEEPSEEK_API_KEY=sk-" not in content and "LLM_API_KEY=sk-" not in content:
        print("[INFO] 未检测到 LLM API key（DEEPSEEK_API_KEY / LLM_API_KEY）")
        print("       Web Demo 和已有数据可正常浏览，但实时抽取/智能问答需要 LLM key")
        print("       请在 .env 中设置 DEEPSEEK_API_KEY=sk-xxx 后重启服务")


def check_database() -> None:
    """检查数据库文件，缺失时初始化。"""
    step("4/6 检查数据库")
    db_file = ROOT / "data" / "bidagent.db"
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if db_file.exists():
        size_mb = db_file.stat().st_size / (1024 * 1024)
        print(f"[OK] 数据库已存在: {db_file} ({size_mb:.2f} MB)")
        # 校验可读
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_file))
            count = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
            fields = conn.execute("SELECT COUNT(*) FROM extracted_fields").fetchone()[0]
            evidence = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            conn.close()
            print(f"     公告数: {count} / 字段数: {fields} / 证据数: {evidence}")
        except Exception as e:
            print(f"[WARN] 数据库读取失败: {e}")
    else:
        print(f"[INFO] 数据库不存在，将通过应用 lifespan 自动初始化: {db_file}")
        print("       首次启动时会自动建表（create_all）")


def check_examples() -> None:
    """检查 examples/ 示例文件可加载。"""
    step("5/6 检查示例文件")
    examples_dir = ROOT / "examples"
    if not examples_dir.exists():
        print(f"[WARN] examples/ 目录不存在: {examples_dir}")
        print("       示例文件非必须，可从 GitHub 获取")
        return

    samples = list(examples_dir.glob("*.json"))
    if not samples:
        print("[WARN] examples/ 下无 JSON 示例文件")
        return

    ok = 0
    for s in samples:
        try:
            data = json.loads(s.read_text(encoding="utf-8"))
            fields = len(data.get("extracted_fields", []))
            evidence = len(data.get("evidence", []))
            print(f"  [OK] {s.name}: {fields} 字段 / {evidence} 证据")
            ok += 1
        except Exception as e:
            print(f"  [FAIL] {s.name}: {e}")

    print(f"[OK] {ok}/{len(samples)} 示例文件可正常加载")


def start_server() -> None:
    """启动 uvicorn 服务。"""
    step("6/6 启动服务")
    host = "0.0.0.0"
    port = 8000
    print(f"启动 uvicorn: http://localhost:{port}")
    print(f"  Web Demo:    http://localhost:{port}/ui")
    print(f"  健康检查:    http://localhost:{port}/health")
    print(f"  API 文档:    http://localhost:{port}/docs")
    print("\n按 Ctrl+C 停止服务\n")

    # 延迟打开浏览器
    try:
        webbrowser.open(f"http://localhost:{port}/ui")
    except Exception:
        pass

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    try:
        subprocess.run(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        print("\n服务已停止")
    except FileNotFoundError:
        print("[FAIL] uvicorn 启动失败，请手动执行:")
        print("       uvicorn app.main:app --reload --host 0.0.0.0 --port 8000")
        sys.exit(1)


def main() -> None:
    print("=" * 50)
    print("标小智 - 可验证招投标数据引擎 | 一键启动")
    print("GOAI 2026 · 无界应用赛道 · AI+金融方向")
    print("=" * 50)

    check_python()
    check_deps()
    check_env()
    check_database()
    check_examples()
    start_server()


if __name__ == "__main__":
    main()
