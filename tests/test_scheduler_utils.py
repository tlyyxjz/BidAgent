"""scheduler.utils.is_cron_due 单元测试 (#15 修复)。

覆盖：
- 空 cron 表达式
- once: 前缀一次性触发
- last_run=None（从未推送）
- 非法 cron 表达式（不抛异常）
- 合法 cron 到期 / 未到期
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.scheduler.utils import is_cron_due


class TestIsCronDue:
    def test_empty_cron_returns_false(self):
        """空串 cron 表达式返回 False。"""
        now = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
        assert is_cron_due("", None, now) is False

    def test_once_prefix_returns_true(self):
        """once: 前缀一次性触发返回 True（无视 last_run）。"""
        now = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
        assert is_cron_due("once:09:00", None, now) is True

    def test_none_last_run_returns_false(self):
        """last_run=None（从未推送）返回 False，等下一个 cron 触发点。"""
        now = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
        assert is_cron_due("0 9 * * *", None, now) is False

    def test_invalid_cron_returns_false(self):
        """非法 cron 表达式返回 False，不抛异常。"""
        last_run = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
        now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
        # 不应抛异常
        result = is_cron_due("not_a_cron", last_run, now)
        assert result is False

    def test_valid_cron_due_returns_true(self):
        """合法 cron 且到期返回 True。

        cron 每天 09:00；上次 08:00；当前 10:00 → 下次 09:00 <= 10:00 到期。
        """
        last_run = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
        now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
        assert is_cron_due("0 9 * * *", last_run, now) is True

    def test_valid_cron_not_due_returns_false(self):
        """合法 cron 但未到期返回 False。

        cron 每天 09:00；上次 10:00；当前 11:00 → 下次 07-28 09:00 > 11:00 未到期。
        """
        last_run = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 7, 27, 11, 0, tzinfo=timezone.utc)
        assert is_cron_due("0 9 * * *", last_run, now) is False
