"""补充测试：scheduler/push.py 与 scheduler/subscription.py 未覆盖路径。

覆盖目标：
- app/scheduler/push.py：push_to_channels 各渠道分支、_push_email 各降级/失败/成功路径、
  _push_webhook 降级/成功路径
- app/scheduler/subscription.py：trigger_subscription 各分支（inactive/not_due/
  no_new/auto_collect 失败/skipped_duplicate/push_failed/ok/force）、_record_push 空列表、
  _compute_content_hash OSError 分支、run_scheduled_subscriptions 各路径

DB 相关测试用真实 SQLite（conftest 已建表）+ 针对性 mock 外部依赖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.database import AsyncSessionLocal
from app.models.subscription import (
    PushLog,
    Subscription,
    TRIGGER_SCHEDULED,
)
from app.models.tender import Tender
from app.models.user import PLAN_FREE, User


# ==== 公共辅助 ====

def _make_sub(**kwargs):
    """构造 mock Subscription 对象。"""
    sub = MagicMock()
    sub.id = kwargs.get("id", 1)
    sub.notify_email = kwargs.get("notify_email", None)
    sub.webhook_url = kwargs.get("webhook_url", None)
    sub.push_channels = kwargs.get("push_channels", None)
    return sub


async def _ensure_user(user_id: int = 1) -> None:
    """确保测试用户存在（FK 约束兜底）。"""
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(User).where(User.id == user_id))
        if existing.scalar_one_or_none() is None:
            db.add(User(id=user_id, email=f"user{user_id}@test.com", plan=PLAN_FREE))
            await db.commit()


async def _insert_subscription(
    *,
    user_id: int = 1,
    raw_query: str = "上海充电桩",
    trigger_type: str = "immediate",
    frequency_cron: str | None = None,
    parsed_filters: dict | None = None,
    is_active: bool = True,
    platforms: list[str] | None = None,
    push_channels: list[str] | None = None,
    notify_email: str | None = None,
    webhook_url: str | None = None,
    last_pushed_at: datetime | None = None,
) -> int:
    """插入一条订阅，返回 sub_id。"""
    await _ensure_user(user_id)
    async with AsyncSessionLocal() as db:
        sub = Subscription(
            user_id=user_id,
            raw_query=raw_query,
            parsed_filters=parsed_filters or {"raw_query": raw_query},
            frequency_cron=frequency_cron,
            trigger_type=trigger_type,
            platforms=platforms or ["ccgp"],
            push_channels=push_channels or ["email"],
            notify_email=notify_email,
            webhook_url=webhook_url,
            is_active=is_active,
            last_pushed_at=last_pushed_at,
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        return sub.id


async def _insert_tenders(
    count: int = 2,
    source_platform: str = "ccgp",
    with_source_text: bool = False,
) -> list[int]:
    """插入若干条招标信息，返回 id 列表。"""
    ids: list[int] = []
    async with AsyncSessionLocal() as db:
        for i in range(count):
            t = Tender(
                project_name=f"上海充电桩采购项目第{i + 1}批",
                source_platform=source_platform,
                source_url=f"http://example.com/t{i + 1}",
                core_content=f"采购充电桩{i + 1}台，预算{i + 1}万元",
                source_raw_text=f"原文内容{i + 1}" if with_source_text else None,
                location="上海",
            )
            db.add(t)
            await db.flush()
            ids.append(t.id)
        await db.commit()
    return ids


# ==== scheduler/push.py 测试 ====

class TestPushToChannels:
    """push_to_channels 各渠道分支测试。"""

    @pytest.mark.asyncio
    async def test_empty_channels_log_fallback(self):
        """无推送渠道时降级为 log，delivered=False。"""
        from app.scheduler.push import push_to_channels

        sub = _make_sub(push_channels=[])
        result = await push_to_channels(sub, "report.docx", 5)

        assert result["delivered"] is False
        assert len(result["channels"]) == 1
        ch = result["channels"][0]
        assert ch["channel"] == "log"
        assert ch["ok"] is True
        assert ch["delivered"] is False
        assert "未配置推送渠道" in ch["error"]

    @pytest.mark.asyncio
    async def test_none_channels_log_fallback(self):
        """push_channels=None 时降级为 log。"""
        from app.scheduler.push import push_to_channels

        sub = _make_sub(push_channels=None)
        result = await push_to_channels(sub, "report.docx", 1)

        assert result["delivered"] is False
        assert result["channels"][0]["channel"] == "log"

    @pytest.mark.asyncio
    async def test_unknown_channel_returns_error(self):
        """未知渠道返回错误，delivered=False。"""
        from app.scheduler.push import push_to_channels

        sub = _make_sub(push_channels=["sms"])
        result = await push_to_channels(sub, "report.docx", 1)

        assert result["delivered"] is False
        ch = result["channels"][0]
        assert ch["channel"] == "sms"
        assert ch["ok"] is False
        assert "未知渠道" in ch["error"]


class TestPushEmail:
    """_push_email 各分支测试。"""

    @pytest.mark.asyncio
    async def test_skipped_when_no_notify_email(self):
        """未配置 notify_email 时降级为 log。"""
        from app.scheduler.push import push_to_channels

        sub = _make_sub(push_channels=["email"], notify_email=None)
        result = await push_to_channels(sub, "report.docx", 1)

        assert result["delivered"] is False
        ch = result["channels"][0]
        assert ch["channel"] == "log"
        assert "notify_email" in ch["error"]

    @pytest.mark.asyncio
    async def test_skipped_when_smtp_not_configured(self):
        """SMTP 未配置时降级为 log。"""
        from app.scheduler import push as push_module

        sub = _make_sub(push_channels=["email"], notify_email="a@example.com")
        with patch.object(push_module._email_sender, "is_configured", return_value=False):
            result = await push_module.push_to_channels(sub, "report.docx", 1)

        assert result["delivered"] is False
        ch = result["channels"][0]
        assert ch["channel"] == "log"
        assert "SMTP" in ch["error"]

    @pytest.mark.asyncio
    async def test_fails_when_attachment_missing(self, tmp_path):
        """附件不存在时返回 email 失败。"""
        from app.scheduler import push as push_module

        sub = _make_sub(push_channels=["email"], notify_email="a@example.com")
        missing = tmp_path / "missing.docx"
        with patch.object(push_module._email_sender, "is_configured", return_value=True):
            result = await push_module.push_to_channels(sub, str(missing), 1)

        assert result["delivered"] is False
        ch = result["channels"][0]
        assert ch["channel"] == "email"
        assert ch["ok"] is False
        assert "报告附件不存在" in ch["error"]

    @pytest.mark.asyncio
    async def test_success_delivered(self, tmp_path):
        """邮件发送成功时 delivered=True。"""
        from app.scheduler import push as push_module

        sub = _make_sub(push_channels=["email"], notify_email="a@example.com")
        report = tmp_path / "report.docx"
        report.write_bytes(b"docx")
        with patch.object(push_module._email_sender, "is_configured", return_value=True), \
                patch.object(
                    push_module._email_sender,
                    "send_with_attachment",
                    new_callable=AsyncMock,
                ) as mock_send:
            mock_send.return_value = {
                "ok": True,
                "message_id": "<msg@example.com>",
                "error": None,
            }
            result = await push_module.push_to_channels(sub, str(report), 3)

        assert result["delivered"] is True
        ch = result["channels"][0]
        assert ch["channel"] == "email"
        assert ch["ok"] is True
        assert ch["delivered"] is True
        assert ch["message_id"] == "<msg@example.com>"
        assert mock_send.await_count == 1
        call_kwargs = mock_send.await_args.kwargs
        assert call_kwargs["to_addrs"] == ["a@example.com"]
        assert "3 条" in call_kwargs["subject"]
        assert call_kwargs["attachment_path"] == report


class TestPushWebhook:
    """_push_webhook 各分支测试。"""

    @pytest.mark.asyncio
    async def test_skipped_when_no_url(self):
        """未配置 webhook_url 时降级为 log。"""
        from app.scheduler.push import push_to_channels

        sub = _make_sub(push_channels=["webhook"], webhook_url=None)
        result = await push_to_channels(sub, "report.docx", 1)

        assert result["delivered"] is False
        ch = result["channels"][0]
        assert ch["channel"] == "log"
        assert "webhook_url" in ch["error"]

    @pytest.mark.asyncio
    async def test_success_delivered(self):
        """webhook 发送成功时 delivered=True。"""
        from app.scheduler import push as push_module

        sub = _make_sub(
            push_channels=["webhook"],
            webhook_url="http://example.com/hook",
        )
        with patch.object(
            push_module._webhook_sender,
            "send",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {
                "ok": True,
                "status_code": 200,
                "error": None,
            }
            result = await push_module.push_to_channels(sub, "report.docx", 2)

        assert result["delivered"] is True
        ch = result["channels"][0]
        assert ch["channel"] == "webhook"
        assert ch["delivered"] is True
        assert mock_send.await_count == 1
        payload = mock_send.await_args.kwargs["payload"]
        assert payload["subscription_id"] == 1
        assert payload["count"] == 2

    @pytest.mark.asyncio
    async def test_failure_not_delivered(self):
        """webhook 发送失败时 delivered=False。"""
        from app.scheduler import push as push_module

        sub = _make_sub(
            push_channels=["webhook"],
            webhook_url="http://example.com/hook",
        )
        with patch.object(
            push_module._webhook_sender,
            "send",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {
                "ok": False,
                "status_code": 500,
                "error": "HTTP 500",
            }
            result = await push_module.push_to_channels(sub, "report.docx", 1)

        assert result["delivered"] is False
        ch = result["channels"][0]
        assert ch["ok"] is False


class TestPushMixedChannels:
    """多渠道聚合 delivered 测试。"""

    @pytest.mark.asyncio
    async def test_mixed_channels_aggregate_delivered(self, tmp_path):
        """email 失败 + webhook 成功 → 整体 delivered=True。"""
        from app.scheduler import push as push_module

        sub = _make_sub(
            push_channels=["email", "webhook"],
            notify_email=None,
            webhook_url="http://example.com/hook",
        )
        report = tmp_path / "r.docx"
        report.write_bytes(b"x")
        with patch.object(
            push_module._webhook_sender,
            "send",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {
                "ok": True,
                "status_code": 200,
                "error": None,
            }
            result = await push_module.push_to_channels(sub, str(report), 1)

        assert result["delivered"] is True
        assert len(result["channels"]) == 2

    @pytest.mark.asyncio
    async def test_all_fail_not_delivered(self, tmp_path):
        """email + webhook 全部失败 → delivered=False。"""
        from app.scheduler import push as push_module

        sub = _make_sub(
            push_channels=["email", "webhook"],
            notify_email=None,
            webhook_url="http://example.com/hook",
        )
        report = tmp_path / "r.docx"
        report.write_bytes(b"x")
        with patch.object(
            push_module._webhook_sender,
            "send",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {
                "ok": False,
                "status_code": 502,
                "error": "HTTP 502",
            }
            result = await push_module.push_to_channels(sub, str(report), 1)

        assert result["delivered"] is False


# ==== scheduler/subscription.py 测试 ====

class TestRecordPush:
    """_record_push 边界测试。"""

    @pytest.mark.asyncio
    async def test_empty_tender_ids_is_noop(self):
        """空 tender_ids 列表时直接返回，不调用 add_all。"""
        from app.scheduler.subscription import _record_push

        db = MagicMock()
        await _record_push(db, 1, [])
        db.add_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_push_logs_for_tender_ids(self):
        """非空 tender_ids 时写入 PushLog。"""
        from app.scheduler.subscription import _record_push

        db = MagicMock()
        await _record_push(db, 5, [10, 20])
        db.add_all.assert_called_once()
        logs = db.add_all.call_args.args[0]
        assert len(logs) == 2
        assert all(log.subscription_id == 5 for log in logs)
        assert {log.tender_id for log in logs} == {10, 20}


class TestComputeContentHashOSError:
    """_compute_content_hash OSError 分支测试。"""

    def test_handles_oserror_gracefully(self, tmp_path, monkeypatch):
        """文件存在但 read_bytes 抛 OSError 时仍返回有效哈希。"""
        from app.scheduler.subscription import _compute_content_hash

        report = tmp_path / "r.docx"
        report.write_bytes(b"data")

        def _raise(self):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_bytes", _raise)
        h = _compute_content_hash(str(report), [1, 2])

        assert len(h) == 64
        monkeypatch.undo()
        h_no_file = _compute_content_hash(str(tmp_path / "absent.docx"), [1, 2])
        assert h == h_no_file


class TestTriggerSubscription:
    """trigger_subscription 各分支测试。"""

    @pytest.mark.asyncio
    async def test_skipped_when_not_found(self):
        """订阅不存在时返回 skipped。"""
        from app.scheduler.subscription import trigger_subscription

        result = await trigger_subscription(999999, auto_collect=False)
        assert result["status"] == "skipped"
        assert "inactive" in result["reason"]

    @pytest.mark.asyncio
    async def test_skipped_when_inactive(self):
        """订阅已停用时返回 skipped。"""
        from app.scheduler.subscription import trigger_subscription

        sub_id = await _insert_subscription(is_active=False)
        result = await trigger_subscription(sub_id, auto_collect=False)
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_skipped_when_user_id_mismatch(self):
        """user_id 不匹配时返回 skipped（覆盖 user_id 过滤分支）。"""
        from app.scheduler.subscription import trigger_subscription

        sub_id = await _insert_subscription(user_id=10)
        result = await trigger_subscription(
            sub_id, user_id=999, auto_collect=False,
        )
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_not_due_when_cron_not_due(self):
        """cron 未到期时返回 not_due。"""
        from app.scheduler.subscription import trigger_subscription

        last = datetime.now(timezone.utc) - timedelta(minutes=5)
        sub_id = await _insert_subscription(
            trigger_type="scheduled",
            frequency_cron="0 9 * * *",
            last_pushed_at=last,
        )
        result = await trigger_subscription(sub_id, auto_collect=False)
        assert result["status"] == "not_due"
        assert "0 9 * * *" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_new_tenders(self):
        """无未推送数据时返回 no_new。"""
        from app.scheduler.subscription import trigger_subscription

        sub_id = await _insert_subscription()
        result = await trigger_subscription(sub_id, auto_collect=False)
        assert result["status"] == "no_new"
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_auto_collect_failure_continues(self, monkeypatch):
        """auto_collect 抛异常时记录错误并继续。"""
        from app.scheduler import subscription as sub_module

        sub_id = await _insert_subscription()

        async def _fail_collect(sub, filters):
            raise RuntimeError("collect boom")

        monkeypatch.setattr(
            "app.scheduler.collector.collect_new_tenders", _fail_collect,
        )
        result = await sub_module.trigger_subscription(sub_id, auto_collect=True)
        assert result["status"] == "no_new"
        assert result["collect"]["error"] == "collect boom"

    @pytest.mark.asyncio
    async def test_force_skips_cron_check(self):
        """force=True 跳过 cron 到期检查。"""
        from app.scheduler.subscription import trigger_subscription

        last = datetime.now(timezone.utc) - timedelta(minutes=5)
        sub_id = await _insert_subscription(
            trigger_type="scheduled",
            frequency_cron="0 9 * * *",
            last_pushed_at=last,
        )
        result = await trigger_subscription(sub_id, force=True, auto_collect=False)
        assert result["status"] == "no_new"

    @pytest.mark.asyncio
    async def test_skipped_duplicate(self, monkeypatch, tmp_path):
        """重复推送（content_hash 命中）返回 skipped_duplicate。"""
        from app.scheduler import subscription as sub_module

        sub_id = await _insert_subscription()
        await _insert_tenders(2)
        report = tmp_path / "report.docx"
        report.write_bytes(b"docx")

        async def _fake_report(*args, **kwargs):
            return str(report)

        monkeypatch.setattr(sub_module, "generate_report", _fake_report)
        monkeypatch.setattr(
            sub_module,
            "_recently_pushed_same_hash",
            AsyncMock(return_value=True),
        )
        result = await sub_module.trigger_subscription(
            sub_id, auto_collect=False,
        )

        assert result["status"] == "skipped_duplicate"
        assert result["count"] == 2
        assert result["content_hash"]
        assert result["report_path"] == str(report)

    @pytest.mark.asyncio
    async def test_push_failed_not_delivered(self, monkeypatch, tmp_path):
        """推送未送达时返回 push_failed 且不写 PushLog。"""
        from app.scheduler import subscription as sub_module

        sub_id = await _insert_subscription()
        await _insert_tenders(2)
        report = tmp_path / "report.docx"
        report.write_bytes(b"docx")

        async def _fake_report(*args, **kwargs):
            return str(report)

        async def _fail_push(sub, path, count):
            return {
                "delivered": False,
                "channels": [
                    {
                        "channel": "email", "ok": False, "delivered": False,
                        "message_id": None, "error": "smtp down",
                    }
                ],
            }

        monkeypatch.setattr(sub_module, "generate_report", _fake_report)
        monkeypatch.setattr(
            sub_module,
            "_recently_pushed_same_hash",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(sub_module, "push_to_channels", _fail_push)
        result = await sub_module.trigger_subscription(
            sub_id, auto_collect=False,
        )

        assert result["status"] == "push_failed"
        assert result["count"] == 2
        async with AsyncSessionLocal() as db:
            logs = (
                await db.execute(
                    select(PushLog).where(PushLog.subscription_id == sub_id)
                )
            ).scalars().all()
            assert len(logs) == 0

    @pytest.mark.asyncio
    async def test_success_writes_pushlog(self, monkeypatch, tmp_path):
        """成功推送时写 PushLog + 更新 last_pushed_at。"""
        from app.scheduler import subscription as sub_module

        sub_id = await _insert_subscription()
        tender_ids = await _insert_tenders(2, with_source_text=True)
        report = tmp_path / "report.docx"
        report.write_bytes(b"docx")

        async def _fake_report(*args, **kwargs):
            return str(report)

        async def _ok_push(sub, path, count):
            return {
                "delivered": True,
                "channels": [
                    {
                        "channel": "email", "ok": True, "delivered": True,
                        "message_id": "m", "error": None,
                    }
                ],
            }

        monkeypatch.setattr(sub_module, "generate_report", _fake_report)
        monkeypatch.setattr(
            sub_module,
            "_recently_pushed_same_hash",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(sub_module, "push_to_channels", _ok_push)
        result = await sub_module.trigger_subscription(
            sub_id, auto_collect=False,
        )

        assert result["status"] == "ok"
        assert result["count"] == 2
        assert result["report_path"] == str(report)
        async with AsyncSessionLocal() as db:
            logs = (
                await db.execute(
                    select(PushLog).where(PushLog.subscription_id == sub_id)
                )
            ).scalars().all()
            assert len(logs) == 2
            assert {log.tender_id for log in logs} == set(tender_ids)
            assert all(log.content_hash for log in logs)
            sub_db = (
                await db.execute(
                    select(Subscription).where(Subscription.id == sub_id)
                )
            ).scalar_one()
            assert sub_db.last_pushed_at is not None


class TestRunScheduledSubscriptions:
    """run_scheduled_subscriptions 各路径测试。"""

    @pytest.mark.asyncio
    async def test_triggers_due_subscription(self, monkeypatch):
        """到期订阅被触发，计数 +1。"""
        from app.scheduler import subscription as sub_module

        sub_id = await _insert_subscription(
            trigger_type="scheduled",
            frequency_cron="0 9 * * *",
        )
        triggered = []

        async def _fake_trigger(sid, force=False, **kwargs):
            triggered.append(sid)
            return {"status": "ok"}

        monkeypatch.setattr(sub_module, "trigger_subscription", _fake_trigger)
        monkeypatch.setattr(sub_module, "is_cron_due", lambda *a, **k: True)

        count = await sub_module.run_scheduled_subscriptions()
        assert count == 1
        assert triggered == [sub_id]

    @pytest.mark.asyncio
    async def test_skips_not_due_subscription(self, monkeypatch):
        """未到期订阅不触发。"""
        from app.scheduler import subscription as sub_module

        await _insert_subscription(
            trigger_type="scheduled",
            frequency_cron="0 9 * * *",
        )
        monkeypatch.setattr(sub_module, "is_cron_due", lambda *a, **k: False)
        monkeypatch.setattr(
            sub_module, "trigger_subscription", AsyncMock(),
        )

        count = await sub_module.run_scheduled_subscriptions()
        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_subscription_with_empty_cron(self, monkeypatch):
        """frequency_cron 为空的 scheduled 订阅被跳过。"""
        from app.scheduler import subscription as sub_module

        await _insert_subscription(
            trigger_type="scheduled",
            frequency_cron=None,
        )
        monkeypatch.setattr(
            sub_module, "trigger_subscription", AsyncMock(),
        )

        count = await sub_module.run_scheduled_subscriptions()
        assert count == 0

    @pytest.mark.asyncio
    async def test_handles_trigger_failure(self, monkeypatch):
        """trigger_subscription 抛异常时不中断扫描，计数为 0。"""
        from app.scheduler import subscription as sub_module

        await _insert_subscription(
            trigger_type="scheduled",
            frequency_cron="0 9 * * *",
        )

        async def _fail_trigger(sid, force=False, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(sub_module, "trigger_subscription", _fail_trigger)
        monkeypatch.setattr(sub_module, "is_cron_due", lambda *a, **k: True)

        count = await sub_module.run_scheduled_subscriptions()
        assert count == 0

    @pytest.mark.asyncio
    async def test_ignores_immediate_subscriptions(self, monkeypatch):
        """immediate 类型订阅不被扫描（仅 scheduled）。"""
        from app.scheduler import subscription as sub_module

        await _insert_subscription(
            trigger_type="immediate",
            frequency_cron=None,
        )
        monkeypatch.setattr(sub_module, "is_cron_due", lambda *a, **k: True)
        monkeypatch.setattr(
            sub_module, "trigger_subscription", AsyncMock(),
        )

        count = await sub_module.run_scheduled_subscriptions()
        assert count == 0
