"""template_monitor.py unit tests (v4.1 sec 5.3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.template_monitor import TemplateMonitor, TemplateSignature


class TestTemplateSignature:
    def test_hash_consistent(self) -> None:
        sig = TemplateSignature(template_name="x", selector_hits={"a": 1}, key_text_hash="abc")
        assert sig.signature_hash() == sig.signature_hash()

    def test_hash_differs_on_hits(self) -> None:
        s1 = TemplateSignature(template_name="x", selector_hits={"a": 1}, key_text_hash="abc")
        s2 = TemplateSignature(template_name="x", selector_hits={"a": 2}, key_text_hash="abc")
        assert s1.signature_hash() != s2.signature_hash()

    def test_hash_differs_on_name(self) -> None:
        s1 = TemplateSignature(template_name="a", selector_hits={}, key_text_hash="")
        s2 = TemplateSignature(template_name="b", selector_hits={}, key_text_hash="")
        assert s1.signature_hash() != s2.signature_hash()


class TestCheck:
    @pytest.mark.asyncio
    async def test_first_check_true(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=MagicMock())
        page.query_selector.return_value.inner_text = AsyncMock(return_value="t")
        assert await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1") is True

    @pytest.mark.asyncio
    async def test_same_structure_false(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock(), MagicMock()])
        page.query_selector = AsyncMock(return_value=MagicMock())
        page.query_selector.return_value.inner_text = AsyncMock(return_value="t")
        await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1")
        assert await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1") is False

    @pytest.mark.asyncio
    async def test_structure_change_true(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock(), MagicMock()])
        page.query_selector = AsyncMock(return_value=MagicMock())
        page.query_selector.return_value.inner_text = AsyncMock(return_value="t")
        await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1")
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        assert await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1") is True

    @pytest.mark.asyncio
    async def test_key_text_change_true(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=MagicMock())
        page.query_selector.return_value.inner_text = AsyncMock(return_value="t1")
        await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1")
        page.query_selector.return_value.inner_text = AsyncMock(return_value="t2")
        assert await tm.check("ccgp", page, {"title": "h1"}, key_selector="h1") is True

    @pytest.mark.asyncio
    async def test_different_templates_independent(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=None)
        await tm.check("ccgp", page, {"title": "h1"})
        assert await tm.check("chinabidding", page, {"title": "h1"}) is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self) -> None:
        tm = TemplateMonitor()
        assert await tm.check("ccgp", None, {"title": "h1"}) is False

    @pytest.mark.asyncio
    async def test_no_key_selector(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=None)
        assert await tm.check("ccgp", page, {"title": "h1"}) is True

    @pytest.mark.asyncio
    async def test_empty_selectors(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])
        page.query_selector = AsyncMock(return_value=None)
        assert await tm.check("ccgp", page, {}) is True


class TestMisc:
    @pytest.mark.asyncio
    async def test_get_signature_none_for_missing(self) -> None:
        tm = TemplateMonitor()
        assert await tm.get_signature("missing") is None

    @pytest.mark.asyncio
    async def test_get_signature_returns(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=None)
        await tm.check("ccgp", page, {"title": "h1"})
        sig = await tm.get_signature("ccgp")
        assert sig is not None
        assert sig.template_name == "ccgp"

    @pytest.mark.asyncio
    async def test_reset(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=None)
        await tm.check("ccgp", page, {"title": "h1"})
        tm.reset()
        assert await tm.get_signature("ccgp") is None

    @pytest.mark.asyncio
    async def test_stats(self) -> None:
        tm = TemplateMonitor()
        page = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[MagicMock()])
        page.query_selector = AsyncMock(return_value=None)
        await tm.check("ccgp", page, {"title": "h1"})
        await tm.check("chinabidding", page, {"title": "h1"})
        assert tm.stats()["templates"] == 2
