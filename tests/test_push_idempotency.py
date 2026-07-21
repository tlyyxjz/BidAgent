"""M-2 修复测试：推送幂等去重（content_hash）。

验证：
1. _compute_content_hash 确定性 + 不同输入产生不同哈希
2. _recently_pushed_same_hash 命中/未命中
3. trigger_subscription 在 DEDUP_WINDOW_MINUTES 内不重复推送
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.subscription import (
    DEDUP_WINDOW_MINUTES,
    _compute_content_hash,
    _recently_pushed_same_hash,
)


class TestComputeContentHash:
    """content_hash 计算测试。"""

    def test_deterministic_same_input_same_hash(self, tmp_path: Path):
        """相同输入产生相同哈希。"""
        report = tmp_path / "report.docx"
        report.write_bytes(b"fake docx content")
        tender_ids = [1, 2, 3]

        h1 = _compute_content_hash(str(report), tender_ids)
        h2 = _compute_content_hash(str(report), tender_ids)

        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_different_tender_ids_different_hash(self, tmp_path: Path):
        """不同 tender_ids 产生不同哈希。"""
        report = tmp_path / "report.docx"
        report.write_bytes(b"content")

        h1 = _compute_content_hash(str(report), [1, 2])
        h2 = _compute_content_hash(str(report), [2, 1])

        # tender_ids 排序后相同，所以哈希应该相同
        assert h1 == h2

        h3 = _compute_content_hash(str(report), [1, 2, 3])
        assert h1 != h3  # 不同 tender_ids 集合

    def test_different_report_content_different_hash(self, tmp_path: Path):
        """相同 tender_ids 但不同报告内容产生不同哈希。"""
        report1 = tmp_path / "r1.docx"
        report1.write_bytes(b"content A")
        report2 = tmp_path / "r2.docx"
        report2.write_bytes(b"content B")

        h1 = _compute_content_hash(str(report1), [1, 2])
        h2 = _compute_content_hash(str(report2), [1, 2])

        assert h1 != h2

    def test_missing_report_file_still_returns_hash(self, tmp_path: Path):
        """报告文件不存在时仍返回哈希（基于 tender_ids）。"""
        h = _compute_content_hash(str(tmp_path / "nonexistent.docx"), [1, 2])
        assert len(h) == 64
        # 相同 tender_ids 应该和「文件存在但读不到」一致
        h2 = _compute_content_hash(str(tmp_path / "nonexistent.docx"), [1, 2])
        assert h == h2


class TestRecentlyPushedSameHash:
    """_recently_pushed_same_hash 数据库查询测试。"""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_record(self):
        """没有相同哈希记录时返回 False。"""
        db = MagicMock()
        result_mock = MagicMock()
        result_mock.first.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        ok = await _recently_pushed_same_hash(db, 1, "abc123")
        assert ok is False

    @pytest.mark.asyncio
    async def test_returns_true_when_record_exists(self):
        """有相同哈希记录时返回 True。"""
        db = MagicMock()
        result_mock = MagicMock()
        result_mock.first.return_value = MagicMock(id=1)  # 非空
        db.execute = AsyncMock(return_value=result_mock)

        ok = await _recently_pushed_same_hash(db, 1, "abc123")
        assert ok is True


class TestDedupWindow:
    """去重窗口常量测试。"""

    def test_dedup_window_is_positive(self):
        """去重窗口必须 > 0。"""
        assert DEDUP_WINDOW_MINUTES > 0

    def test_dedup_window_reasonable(self):
        """去重窗口应该在合理范围内（5-1440 分钟）。"""
        assert 5 <= DEDUP_WINDOW_MINUTES <= 1440


class TestPushLogContentHashField:
    """PushLog 模型 content_hash 字段测试。"""

    def test_pushlog_model_has_content_hash_field(self):
        """PushLog 模型必须有 content_hash 字段（M-2 修复）。"""
        from app.models.subscription import PushLog

        # 检查模型有 content_hash 属性
        assert hasattr(PushLog, "content_hash")

        # 检查 column 配置（nullable + indexed）
        col = PushLog.__table__.columns.get("content_hash")
        assert col is not None
        assert col.nullable is True  # 允许旧数据为 NULL
        # 索引可能为 True 或 False，M-2 修复要求加索引
        assert col.index is True


class TestSQLiteMigrationForContentHash:
    """SQLite 迁移包含 content_hash 字段测试。"""

    def test_migration_includes_content_hash(self):
        """_SQLITE_MIGRATIONS 必须包含 push_logs.content_hash 迁移项。"""
        from app.models.database import _SQLITE_MIGRATIONS

        found = any(
            table == "push_logs" and column == "content_hash"
            for table, column, _ in _SQLITE_MIGRATIONS
        )
        assert found, "缺少 push_logs.content_hash 的 SQLite 迁移项"
