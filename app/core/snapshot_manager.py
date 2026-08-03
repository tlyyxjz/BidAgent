"""Page snapshot and version manager (v4.1 sec 5.3 + sec 4.7).

Responsibilities:
- Save page snapshots (raw HTML / clean text) with SHA256 content hash.
- Detect content changes by comparing content_hash.
- Create new version only when content changes (avoid redundant storage).
- Mark material=true for business field changes.
- Write snapshots to disk under data/snapshots/.
- Never overwrite historical versions (sec 4.7).

Design:
- In-memory index + on-disk snapshot files.
- Coroutine-safe (asyncio.Lock protects version index).
- Disk I/O offloaded to thread pool via run_in_executor.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("snapshot_manager")


def _get_snapshot_dir() -> Path:
    """Get snapshot storage directory."""
    base = Path(settings.ATTACHMENT_DIR).parent / "snapshots"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class SnapshotRecord:
    """Snapshot record for a page version."""

    url: str
    content_hash: str
    snapshot_path: Optional[str] = None
    fetched_at: float = field(default_factory=time.time)
    is_new_version: bool = False
    material: bool = False
    version_number: int = 1

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "content_hash": self.content_hash,
            "snapshot_path": self.snapshot_path,
            "fetched_at": self.fetched_at,
            "is_new_version": self.is_new_version,
            "material": self.material,
            "version_number": self.version_number,
        }


class SnapshotManager:
    """Page snapshot and version manager.

    Saves snapshots with SHA256 hash; creates new version on content change.
    """

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self._storage_dir: Path = storage_dir or _get_snapshot_dir()
        self._versions: dict[str, list[SnapshotRecord]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def save_snapshot(
        self,
        url: str,
        html: str,
        text: Optional[str] = None,
        material: bool = False,
    ) -> SnapshotRecord:
        """Save page snapshot.

        If content hash matches latest, only update check time (no new version).
        If content changed, create new version and write to disk.

        Args:
            url: Page URL.
            html: Raw HTML content.
            text: Cleaned plain text (optional; used for hash if provided).
            material: Whether to mark as business field change.

        Returns:
            SnapshotRecord: The snapshot record.
        """
        hash_content = text if text else html
        content_hash = hashlib.sha256(hash_content.encode("utf-8")).hexdigest()

        async with self._lock:
            versions = self._versions.get(url, [])
            latest = versions[-1] if versions else None

            if latest and latest.content_hash == content_hash:
                latest.fetched_at = time.time()
                latest.is_new_version = False
                logger.debug("snapshot unchanged url=%s hash=%s", url[:80], content_hash[:16])
                return latest

            version_number = len(versions) + 1
            snapshot_path = await self._write_to_disk(url, version_number, html)

            record = SnapshotRecord(
                url=url,
                content_hash=content_hash,
                snapshot_path=str(snapshot_path) if snapshot_path else None,
                is_new_version=True,
                material=material,
                version_number=version_number,
            )
            versions.append(record)
            self._versions[url] = versions
            logger.info(
                "snapshot new version url=%s v%d hash=%s material=%s",
                url[:80], version_number, content_hash[:16], material,
            )
            return record

    async def _write_to_disk(
        self, url: str, version: int, content: str
    ) -> Optional[Path]:
        """Write snapshot to disk (offloaded to thread pool)."""
        try:
            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            filename = f"{url_hash}_v{version}.html"
            filepath = self._storage_dir / filename

            def _write():
                filepath.write_text(content, encoding="utf-8")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _write)
            return filepath
        except OSError as exc:
            logger.warning("snapshot write failed url=%s err=%s", url[:80], exc)
            return None

    async def get_latest(self, url: str) -> Optional[SnapshotRecord]:
        """Get latest snapshot record for a URL."""
        async with self._lock:
            versions = self._versions.get(url, [])
            return versions[-1] if versions else None

    async def get_history(self, url: str) -> list[SnapshotRecord]:
        """Get all historical versions for a URL."""
        async with self._lock:
            return list(self._versions.get(url, []))

    async def mark_material(self, url: str, version: int) -> bool:
        """Mark a specific version as material (business field change)."""
        async with self._lock:
            versions = self._versions.get(url, [])
            for v in versions:
                if v.version_number == version:
                    v.material = True
                    return True
            return False

    def reset(self) -> None:
        """Clear in-memory index (test helper; does not delete disk files)."""
        self._versions.clear()

    def stats(self) -> dict[str, int]:
        """Return snapshot stats."""
        total_versions = sum(len(vs) for vs in self._versions.values())
        return {
            "urls": len(self._versions),
            "total_versions": total_versions,
        }


snapshot_manager = SnapshotManager()
