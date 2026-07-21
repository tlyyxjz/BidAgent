# =====================================================================

"""安全 Webhook 推送。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url_async

logger = get_logger("webhook_sender")


class WebhookSender:
    """带 SSRF 防护和 HMAC 签名的 Webhook Sender。"""

    def __init__(
        self,
        timeout: float = 10,
        max_attempts: int = 3,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        if max_attempts <= 0:
            raise ValueError("max_attempts 必须 > 0")
        self.timeout = timeout
        self.max_attempts = max_attempts

    @staticmethod
    def _serialize(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _signature(body: bytes, timestamp: str) -> str:
        signed = timestamp.encode("ascii") + b"." + body
        digest = hmac.new(
            settings.WEBHOOK_SECRET.encode("utf-8"),
            signed,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={digest}"

    async def send(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        safe, reason = await is_safe_url_async(url)
        if not safe:
            return {
                "ok": False,
                "status_code": None,
                "error": f"Webhook URL 不安全: {reason}",
            }

        body = self._serialize(payload)
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-ScrapeFlow-Timestamp": timestamp,
            "X-ScrapeFlow-Signature": self._signature(
                body,
                timestamp,
            ),
        }

        last_error: str | None = None
        last_status: int | None = None

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
        ) as client:
            for attempt in range(self.max_attempts):
                try:
                    response = await client.post(
                        url,
                        content=body,
                        headers=headers,
                    )
                    last_status = response.status_code

                    if 200 <= response.status_code < 300:
                        return {
                            "ok": True,
                            "status_code": response.status_code,
                            "error": None,
                        }

                    last_error = (
                        f"HTTP {response.status_code}"
                    )
                except httpx.HTTPError as exc:
                    last_error = (
                        f"{type(exc).__name__}: {exc}"
                    )

                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(2 ** attempt)

        logger.warning(
            "webhook failed status={} error={}",
            last_status,
            last_error,
        )
        return {
            "ok": False,
            "status_code": last_status,
            "error": last_error,
        }
