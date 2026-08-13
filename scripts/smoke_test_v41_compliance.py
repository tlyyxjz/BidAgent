"""v4.1 合规采集层冒烟测试（真实调用，非 mock）。

按 Sol 规矩：冒烟测试必须记录，必须跑真实数据。
验证 6 个组件的端到端功能：
1. credentials.py - Argon2id/AES-GCM 加解密往返
2. rate_limiter.py - 域名级频率限制
3. robots_checker.py - robots.txt 解析（mock httpx，避免真实网络）
4. cache_manager.py - ETag/304 缓存
5. snapshot_manager.py - 快照/哈希/版本管理
6. template_monitor.py - 结构签名（mock page）

运行方式：
    cd <仓库根目录>
    python scripts/smoke_test_v41_compliance.py

输出：
    - 控制台打印每项结果
    - 写入 _w3_outputs/smoke_v41_compliance.json
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 设置环境变量（必须在 import app 之前）
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-12345")

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from scripts.smoke_test_v41_runner import SmokeTestRunnerBase
from scripts.smoke_test_v41_tests import SmokeTestTestsMixin


class SmokeTestRunner(SmokeTestTestsMixin, SmokeTestRunnerBase):
    """v4.1 合规采集层冒烟测试运行器（组合 mixin + base）。"""
    pass


if __name__ == "__main__":
    runner = SmokeTestRunner()
    success = asyncio.run(runner.run_all())
    sys.exit(0 if success else 1)
