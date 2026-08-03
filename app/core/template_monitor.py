"""Page template structure change monitor (v4.1 sec 5.3).

Responsibilities:
- Detect if page CSS selector structure has changed.
- Record a "structure signature" per template (selector hit count + key text hash).
- Alert when signature changes (template may need update).
- Never block scraping flow (detection failure only warns, does not raise).

Design:
- In-memory signatures, coroutine-safe.
- Signature = SHA256(template_name + sorted(selector_hits) + key_text_hash).
- On change: log warning with old/new signature hashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.utils.logger import get_logger

logger = get_logger("template_monitor")


@dataclass
class TemplateSignature:
    """Template structure signature."""

    template_name: str
    selector_hits: dict[str, int]
    key_text_hash: str
    checked_at: float = field(default_factory=time.time)

    def signature_hash(self) -> str:
        """Compute hash of this signature for fast comparison."""
        sig_str = f"{self.template_name}|{sorted(self.selector_hits.items())}|{self.key_text_hash}"
        return hashlib.sha256(sig_str.encode("utf-8")).hexdigest()[:16]


class TemplateMonitor:
    """Page template structure change monitor.

    Records structure signatures per template and detects changes.
    """

    def __init__(self) -> None:
        self._signatures: dict[str, TemplateSignature] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def check(
        self,
        template_name: str,
        page: Any,
        selectors: dict[str, str],
        key_selector: Optional[str] = None,
    ) -> bool:
        """Check if template structure has changed.

        Args:
            template_name: Template name (e.g. "ccgp", "chinabidding").
            page: Playwright Page object.
            selectors: Template selectors dict {field: selector}.
            key_selector: Key element selector for text hash.

        Returns:
            bool: True if structure changed (or first record); False if unchanged.
        """
        if page is None:
            logger.warning("template_monitor check failed name=%s err=page is None", template_name)
            return False
        try:
            selector_hits: dict[str, int] = {}
            for field_name, sel in selectors.items():
                try:
                    elements = await page.query_selector_all(sel)
                    selector_hits[field_name] = len(elements)
                except Exception:  # noqa: BLE001
                    selector_hits[field_name] = 0

            key_text = ""
            if key_selector:
                try:
                    el = await page.query_selector(key_selector)
                    if el:
                        key_text = await el.inner_text()
                except Exception:  # noqa: BLE001
                    pass
            key_text_hash = hashlib.sha256(
                key_text.encode("utf-8")
            ).hexdigest()[:16]

            new_sig = TemplateSignature(
                template_name=template_name,
                selector_hits=selector_hits,
                key_text_hash=key_text_hash,
            )

            async with self._lock:
                old_sig = self._signatures.get(template_name)
                self._signatures[template_name] = new_sig

                if old_sig is None:
                    logger.info(
                        "template_monitor first record name=%s sig=%s",
                        template_name, new_sig.signature_hash(),
                    )
                    return True

                if old_sig.signature_hash() != new_sig.signature_hash():
                    logger.warning(
                        "template_monitor structure changed name=%s old=%s new=%s",
                        template_name,
                        old_sig.signature_hash(),
                        new_sig.signature_hash(),
                    )
                    return True

                return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "template_monitor check failed name=%s err=%s",
                template_name, exc,
            )
            return False

    async def get_signature(self, template_name: str) -> Optional[TemplateSignature]:
        """Get current signature for a template."""
        async with self._lock:
            return self._signatures.get(template_name)

    def reset(self) -> None:
        """Clear all signatures (test helper)."""
        self._signatures.clear()

    def stats(self) -> dict[str, int]:
        """Return monitor stats."""
        return {"templates": len(self._signatures)}


template_monitor = TemplateMonitor()
