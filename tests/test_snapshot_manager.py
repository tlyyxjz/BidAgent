"""snapshot_manager.py unit tests (v4.1 sec 5.3 + sec 4.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.snapshot_manager import SnapshotManager, SnapshotRecord


class TestSaveSnapshot:
    @pytest.mark.asyncio
    async def test_first_save_new_version(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        record = await sm.save_snapshot("http://x.com", "<html>v1</html>")
        assert record.is_new_version is True
        assert record.version_number == 1
        assert len(record.content_hash) == 64

    @pytest.mark.asyncio
    async def test_same_content_no_new_version(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        r1 = await sm.save_snapshot("http://x.com", "<html>v1</html>")
        r2 = await sm.save_snapshot("http://x.com", "<html>v1</html>")
        assert r2.is_new_version is False
        assert r2.version_number == 1

    @pytest.mark.asyncio
    async def test_changed_content_new_version(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        r1 = await sm.save_snapshot("http://x.com", "<html>v1</html>")
        r2 = await sm.save_snapshot("http://x.com", "<html>v2</html>")
        assert r2.version_number == 2
        assert r2.is_new_version is True
        assert r1.content_hash != r2.content_hash

    @pytest.mark.asyncio
    async def test_text_used_for_hash(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        r1 = await sm.save_snapshot("http://x.com", "<html>v1</html>", text="v1")
        r2 = await sm.save_snapshot("http://x.com", "<div>v1</div>", text="v1")
        assert r2.is_new_version is False
        assert r1.content_hash == r2.content_hash

    @pytest.mark.asyncio
    async def test_material_flag(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        record = await sm.save_snapshot("http://x.com", "<html>v1</html>", material=True)
        assert record.material is True

    @pytest.mark.asyncio
    async def test_disk_file_created(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        record = await sm.save_snapshot("http://x.com", "<html>v1</html>")
        assert record.snapshot_path is not None
        path = Path(record.snapshot_path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "<html>v1</html>"


class TestGetHistory:
    @pytest.mark.asyncio
    async def test_get_history_all_versions(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        await sm.save_snapshot("http://x.com", "<html>v1</html>")
        await sm.save_snapshot("http://x.com", "<html>v2</html>")
        await sm.save_snapshot("http://x.com", "<html>v3</html>")
        history = await sm.get_history("http://x.com")
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_get_latest(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        await sm.save_snapshot("http://x.com", "<html>v1</html>")
        await sm.save_snapshot("http://x.com", "<html>v2</html>")
        latest = await sm.get_latest("http://x.com")
        assert latest is not None
        assert latest.version_number == 2

    @pytest.mark.asyncio
    async def test_get_latest_none_for_missing(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        assert await sm.get_latest("http://missing.com") is None

    @pytest.mark.asyncio
    async def test_get_history_empty_for_missing(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        assert await sm.get_history("http://missing.com") == []


class TestMarkMaterial:
    @pytest.mark.asyncio
    async def test_mark_existing(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        await sm.save_snapshot("http://x.com", "<html>v1</html>")
        await sm.save_snapshot("http://x.com", "<html>v2</html>")
        result = await sm.mark_material("http://x.com", 1)
        assert result is True
        history = await sm.get_history("http://x.com")
        assert history[0].material is True
        assert history[1].material is False

    @pytest.mark.asyncio
    async def test_mark_nonexistent(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        result = await sm.mark_material("http://x.com", 99)
        assert result is False


class TestStatsReset:
    @pytest.mark.asyncio
    async def test_stats(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        await sm.save_snapshot("http://a.com", "<html>a1</html>")
        await sm.save_snapshot("http://a.com", "<html>a2</html>")
        await sm.save_snapshot("http://b.com", "<html>b1</html>")
        stats = sm.stats()
        assert stats["urls"] == 2
        assert stats["total_versions"] == 3

    @pytest.mark.asyncio
    async def test_reset(self, tmp_path: Path) -> None:
        sm = SnapshotManager(storage_dir=tmp_path)
        await sm.save_snapshot("http://x.com", "<html>v1</html>")
        sm.reset()
        assert await sm.get_latest("http://x.com") is None


class TestSnapshotRecord:
    def test_to_dict(self) -> None:
        record = SnapshotRecord(url="http://x.com", content_hash="a" * 64, version_number=1)
        d = record.to_dict()
        assert d["url"] == "http://x.com"
        assert d["version_number"] == 1
