from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.email_sender as module
from app.config import settings
from app.core.email_sender import (
    EmailSender,
    _normalize_recipients,
    _sanitize_header,
)


@pytest.fixture
def smtp_config(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "password")
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(
        settings,
        "SMTP_FROM_ADDR",
        "sender@example.com",
    )
    monkeypatch.setattr(
        settings,
        "SMTP_FROM_NAME",
        "标小智",
    )
    monkeypatch.setattr(settings, "SMTP_TIMEOUT", 10)


def test_header_injection_rejected():
    """邮件头中的换行符必须被拒绝。"""
    with pytest.raises(ValueError):
        _sanitize_header("hello\r\nBcc: x@x.com", "subject")


def test_recipients_are_deduplicated():
    """收件人应按大小写不敏感方式去重。"""
    assert _normalize_recipients([
        "A@example.com",
        "a@example.com",
        "b@example.com",
    ]) == ["A@example.com", "b@example.com"]


def test_invalid_recipient_rejected():
    """明显无效的邮箱地址应被拒绝。"""
    with pytest.raises(ValueError):
        _normalize_recipients(["invalid-address"])


@pytest.mark.asyncio
async def test_unconfigured_smtp_returns_failure(
    monkeypatch,
    tmp_path: Path,
):
    """SMTP 未配置时不应建立连接。"""
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    result = await EmailSender().send_with_attachment(
        ["a@example.com"],
        "subject",
        "body",
        tmp_path / "missing.docx",
    )
    assert not result["ok"]
    assert "配置不完整" in result["error"]


@pytest.mark.asyncio
async def test_missing_attachment_rejected(
    smtp_config,
    tmp_path: Path,
):
    """附件不存在时不得连接 SMTP。"""
    result = await EmailSender().send_with_attachment(
        ["a@example.com"],
        "subject",
        "body",
        tmp_path / "missing.docx",
    )
    assert not result["ok"]
    assert "附件不存在" in result["error"]


@pytest.mark.asyncio
async def test_success_returns_message_id(
    monkeypatch,
    smtp_config,
    tmp_path: Path,
):
    """成功发送必须返回 Message-ID。"""
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")
    monkeypatch.setattr(
        EmailSender,
        "_send_sync",
        MagicMock(return_value=None),
    )

    result = await EmailSender().send_with_attachment(
        ["a@example.com"],
        "subject",
        "body",
        path,
    )
    assert result["ok"]
    assert result["message_id"].startswith("<")
    assert result["error"] is None


@pytest.mark.asyncio
async def test_retries_three_times(
    monkeypatch,
    smtp_config,
    tmp_path: Path,
):
    """首次失败后最多重试 3 次。"""
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")

    send = MagicMock(side_effect=OSError("offline"))
    sleep = AsyncMock()
    monkeypatch.setattr(EmailSender, "_send_sync", send)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    result = await EmailSender().send_with_attachment(
        ["a@example.com"],
        "subject",
        "body",
        path,
    )

    assert not result["ok"]
    assert send.call_count == 4
    assert [call.args[0] for call in sleep.await_args_list] == [
        1,
        2,
        4,
    ]


def test_starttls_path_uses_smtp(monkeypatch, smtp_config):
    """TLS=True 时使用 SMTP.starttls。"""
    server = MagicMock()
    # sendmail() 返回空字典（无被拒收件人），避免 _raise_if_refused 抛异常
    server.sendmail.return_value = {}
    context_manager = MagicMock()
    context_manager.__enter__.return_value = server
    smtp = MagicMock(return_value=context_manager)
    monkeypatch.setattr(module.smtplib, "SMTP", smtp)

    config = EmailSender._config()
    EmailSender._send_via_starttls(
        config,
        ["a@example.com"],
        MagicMock(as_string=lambda: "message"),
    )

    server.starttls.assert_called_once()
    server.login.assert_called_once()
    server.sendmail.assert_called_once()


def test_ssl_path_uses_smtp_ssl(
    monkeypatch,
    smtp_config,
):
    """TLS=False 路径必须使用 SMTP_SSL。"""
    monkeypatch.setattr(settings, "SMTP_USE_TLS", False)
    server = MagicMock()
    # sendmail() 返回空字典（无被拒收件人），避免 _raise_if_refused 抛异常
    server.sendmail.return_value = {}
    context_manager = MagicMock()
    context_manager.__enter__.return_value = server
    smtp_ssl = MagicMock(return_value=context_manager)
    monkeypatch.setattr(
        module.smtplib,
        "SMTP_SSL",
        smtp_ssl,
    )

    config = EmailSender._config()
    EmailSender._send_via_ssl(
        config,
        ["a@example.com"],
        MagicMock(as_string=lambda: "message"),
    )

    smtp_ssl.assert_called_once()
    server.login.assert_called_once()
    server.sendmail.assert_called_once()


@pytest.mark.asyncio
async def test_subject_injection_rejected(
    smtp_config,
    tmp_path: Path,
):
    """Subject 注入在连接 SMTP 前被拒绝。"""
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")

    result = await EmailSender().send_with_attachment(
        ["a@example.com"],
        "ok\r\nBcc: evil@example.com",
        "body",
        path,
    )
    assert not result["ok"]
    assert "非法换行符" in result["error"]
