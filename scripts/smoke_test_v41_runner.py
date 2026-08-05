"""v4.1 合规采集层冒烟测试运行器基类（记录 + 汇总）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 确保输出目录存在
OUTPUT_DIR = PROJECT_ROOT / "_w3_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class SmokeTestRunnerBase:
    """冒烟测试运行器基类（记录 + 汇总）。"""

    def __init__(self) -> None:
        self.results: list[dict] = []
        self.passed = 0
        self.failed = 0

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        """记录测试结果。"""
        status = "PASS" if passed else "FAIL"
        self.results.append({
            "name": name,
            "status": status,
            "detail": detail,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if passed:
            self.passed += 1
            print(f"  [PASS] {name}: {detail}")
        else:
            self.failed += 1
            print(f"  [FAIL] {name}: {detail}")

    async def run_all(self) -> None:
        """运行所有冒烟测试。"""
        print("=" * 60)
        print("v4.1 合规采集层冒烟测试（真实调用，非 mock）")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"时间: {ts}")
        print(f"Python: {sys.version.split()[0]}")
        print("=" * 60)

        await self.test_credentials()
        await self.test_rate_limiter()
        await self.test_robots_checker()
        await self.test_cache_manager()
        await self.test_snapshot_manager()
        await self.test_template_monitor()

        print("\n" + "=" * 60)
        print(f"冒烟测试结果: {self.passed} PASS / {self.failed} FAIL")
        print("=" * 60)

        # 写入 JSON 报告
        report = {
            "test_name": "v4.1_compliance_smoke",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python_version": sys.version.split()[0],
            "total": len(self.results),
            "passed": self.passed,
            "failed": self.failed,
            "results": self.results,
        }
        report_path = OUTPUT_DIR / "smoke_v41_compliance.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {report_path}")

        return self.failed == 0
