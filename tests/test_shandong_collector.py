"""山东省政府采购网采集器离线夹具测试（collect_shandong.py）。

夹具基于已逆向契约构造（D6 实机核对后新增双层包装夹具）：
- 列表：POST getListByCode → {"records":[...]}（旧扁平，仍兼容）
  或 D6 实机形态 {status,message,data:{code,message,data:{records}}}
- 详情：GET getDetail → 双层包装内 base64 body（实机）；兼容 {"noticeBody":html} /
  {"data":{...}} / 裸 HTML 旧形态
"""
import base64
import json
import sys
import types
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import collect_shandong as cs  # noqa: E402

# ------------------------------------------------------------ 契约夹具

# 列表契约示例（含空 id/空 title 脏记录）
LIST_PAYLOAD = {
    "records": [
        {
            "id": "9f8e7d6c5b4a",
            "colCode": "29",
            "title": "济南大学实验设备采购项目中标公告",
            "date": "2026-08-15 10:30:00",
            "areaName": "济南市",
            "userName": "济南大学",
        },
        {"id": "", "colCode": "29", "title": "空ID脏记录", "date": "2026-08-14"},
        {"id": "bad001", "colCode": "29", "title": "  ", "date": "2026-08-14"},
        {
            "id": "aabbccdd",
            "colCode": "29",
            "title": "  青岛市某单位   服务器采购成交公告 ",
            "date": "2026/08/14",
            "areaName": "青岛市",
        },
    ]
}
_LIST_TEXT = json.dumps(LIST_PAYLOAD, ensure_ascii=False)  # 避免方法参数 json 遮蔽模块

# 详情契约示例：noticeBody 为 HTML 字符串
DETAIL_BODY_HTML = (
    "<html><head><title>济南大学实验设备采购项目中标公告</title></head><body>"
    "<p>一、项目编号：SDGP37000000202602001234</p>"
    "<p>采购单位济南大学行政区域济南市</p>"
    "<p>二、中标供应商：山东示例科技有限公司</p>"
    "<p>三、总中标金额：123.4567 万元</p>"
    "<p>发布日期：2026-08-15 10:30</p>"
    "</body></html>"
)


# ---------------------------------------------------------------- 列表解析

def test_parse_list_json_contract_shape():
    items = cs.parse_list_json(LIST_PAYLOAD)
    assert len(items) == 2  # 空 id / 空 title 被过滤
    assert items[0]["id"] == "9f8e7d6c5b4a"
    assert items[0]["col_code"] == "29"
    assert items[0]["title"] == "济南大学实验设备采购项目中标公告"
    assert items[0]["publish_dt"] == datetime(2026, 8, 15, 10, 30, 0)
    # 标题空白归一 + 斜杠日期解析
    assert items[1]["title"] == "青岛市某单位 服务器采购成交公告"
    assert items[1]["publish_dt"] == datetime(2026, 8, 14)


def test_parse_list_json_unexpected_shapes():
    assert cs.parse_list_json(None) == []
    assert cs.parse_list_json("not-a-dict") == []
    assert cs.parse_list_json({"code": 500}) == []
    assert cs.parse_list_json({"records": "oops"}) == []
    assert cs.parse_list_json({"records": [None, {"id": "x"}]}) == []


def test_parse_list_json_real_machine_wrapped():
    """D6 实机契约：外层 {status,data:{code,data:{records}}} 双层包装。"""
    wrapped = {
        "timestamp": "2026-08-19T10:51:37.252+00:00", "status": 200,
        "error": "", "exception": "", "message": "OK",
        "path": "/api/website/site/getListByCode",
        "data": {"code": 100, "message": "接口调用成功并成功返回",
                 "data": LIST_PAYLOAD},
    }
    items = cs.parse_list_json(wrapped)
    assert len(items) == 2  # 与扁平形态解析结果一致
    assert items[0]["id"] == "9f8e7d6c5b4a"


def test_api_body_unwrap_variants():
    """_api_body：双层/单层/非 dict 的解包守卫。"""
    assert cs._api_body({"data": {"code": 100, "data": {"records": []}}}) == {"records": []}
    assert cs._api_body({"code": 100, "data": {"records": []}}) == {"records": []}
    assert cs._api_body(None) == {}
    assert cs._api_body("x") == {}


# ---------------------------------------------------------------- 详情解析

def test_parse_detail_html_three_forms():
    # 形态 1：{"noticeBody": html}
    assert cs.parse_detail_html(json.dumps({"noticeBody": DETAIL_BODY_HTML})) == DETAIL_BODY_HTML
    # 形态 2：{"data": {"noticeBody": html}}
    wrapped = json.dumps({"data": {"noticeBody": DETAIL_BODY_HTML}}, ensure_ascii=False)
    assert cs.parse_detail_html(wrapped) == DETAIL_BODY_HTML
    # 形态 3：裸 HTML（非 JSON 直接透传）
    assert cs.parse_detail_html(DETAIL_BODY_HTML) == DETAIL_BODY_HTML


def test_parse_detail_html_real_machine_b64():
    """D6 实机：双层包装 + base64 body。"""
    import base64
    b64 = base64.b64encode(DETAIL_BODY_HTML.encode("utf-8")).decode("ascii")
    payload = {
        "timestamp": "x", "status": 200, "message": "OK",
        "data": {"code": 100, "message": "ok",
                 "data": {"title": "t", "date": "2026-08-19",
                          "userName": "u", "body": b64}},
    }
    assert cs.parse_detail_html(json.dumps(payload, ensure_ascii=False)) == DETAIL_BODY_HTML


def test_parse_detail_html_code999_returns_empty():
    """D6 实机：接口抖动 code=999（data=null）→ 空串，由采集器跳过。"""
    payload = {"status": 200, "message": "OK",
               "data": {"code": 999, "message": "抱歉，服务请求失败，请稍后重试。",
                        "data": None, "success": False}, "success": True}
    assert cs.parse_detail_html(json.dumps(payload, ensure_ascii=False)) == ""


def test_maybe_b64_decode_guard():
    """非 base64/含标签/解出无标签 → 原样返回。"""
    assert cs._maybe_b64_decode("<p>x</p>") == "<p>x</p>"
    assert cs._maybe_b64_decode("短") == "短"
    assert cs._maybe_b64_decode("") == ""


def test_maybe_b64_decode_gbk_no_charset_meta():
    """D6 probe16 实锤：正文 GBK 且无 charset 声明，utf-8 失败回退 gb18030。"""
    import base64
    raw = "<div>中标供应商：泰安市威新医用制品有限公司</div>".encode("gb18030")
    assert "中标" in cs._maybe_b64_decode(base64.b64encode(raw).decode("ascii"))
    # 有 meta 声明时按声明解
    raw2 = '<meta charset="utf-8"><p>中文</p>'.encode("utf-8")
    assert "中文" in cs._maybe_b64_decode(base64.b64encode(raw2).decode("ascii"))


# D6 probe17 实机样本：山东结果公告为表格展平格式（无冒号型字段）
_SD_TABLE_TEXT = (
    "一、项目编号：SDGP370000000202602003681-1 二、项目名称：医疗设备购置项目(二次) "
    "三、采购结果 采购包1: 供应商名称 供应商地址 中标（成交）金额 评审总得分 "
    "泰安市威新医用制品有限公司 山东省济南市槐荫区经三路289号 1,992,000.00元 82.07 "
    "四、主要标的信息 代理服务费金额： 合同包1： 19434元。 "
    "1.采购人信息 名称： 山东中医药大学附属医院(省中医院)"
)


def test_sd_extract_winners_table_format():
    """共享解析器对表格展平格式未命中 → 山东 fallback 命中标头后首个公司名。"""
    assert cs._extract_winners_from_text(_SD_TABLE_TEXT) == []
    assert cs.sd_extract_winners(_SD_TABLE_TEXT) == ["泰安市威新医用制品有限公司"]


def test_sd_extract_win_amount_table_format():
    """表头后首个金额（千分位），不误取后文代理服务费 19434元。"""
    assert cs.parse_win_amount(_SD_TABLE_TEXT) is None
    assert cs.sd_extract_win_amount(_SD_TABLE_TEXT) == Decimal("1992000.00")
    assert cs.sd_extract_win_amount("无金额文本") is None


def test_build_payload_real_machine_table_format():
    """D6 实机链路：表格展平正文走 build_payload 后中标人/金额/编号非 None。"""
    item = {"id": "n1", "col_code": "0302",
            "title": "医疗设备购置项目(二次)结果公告（采购包1）",
            "date": "2026-08-19", "publish_dt": datetime(2026, 8, 19)}
    p = cs.build_payload(item, _SD_TABLE_TEXT)
    assert p["bid_number"] == "SDGP370000000202602003681-1"
    assert p["win_company"] == "泰安市威新医用制品有限公司"
    assert p["win_amount"] == Decimal("1992000.00")
    assert p["notice_type"] == "award"


def test_parse_detail_title():
    assert cs.parse_detail_title(DETAIL_BODY_HTML) == "济南大学实验设备采购项目中标公告"
    assert cs.parse_detail_title("<div>无 title</div>") is None


def test_strip_tags_keeps_field_text():
    text = cs.strip_tags(DETAIL_BODY_HTML)
    assert "SDGP37000000202602001234" in text
    assert "中标供应商" in text
    assert "123.4567" in text
    assert "<p>" not in text


def test_build_payload_full_fields():
    items = cs.parse_list_json(LIST_PAYLOAD)
    p = cs.build_payload(items[0], DETAIL_BODY_HTML)
    assert p["project_name"] == "济南大学实验设备采购项目中标公告"
    assert p["bid_number"] == "SDGP37000000202602001234"
    assert p["win_amount"] == Decimal("1234567")
    assert p["tender_org"] == "济南大学"
    assert p["win_company"] == "山东示例科技有限公司"
    assert p["notice_type"] == "award"
    assert p["source_platform"] == "shandong"
    assert p["source_url"] == (
        "https://www.ccgp-shandong.gov.cn/detail"
        "?id=9f8e7d6c5b4a&colCode=29"
    )
    assert p["publish_time"] == datetime(2026, 8, 15, 10, 30)
    assert isinstance(p["simhash"], int) and -(2 ** 63) <= p["simhash"] < 2 ** 63
    assert "中标供应商" in p["source_raw_text"]
    assert len(p["core_content"]) <= 2000


def test_build_payload_publish_time_fallback_to_list_date():
    """详情无"发布日期"时回落列表 date（与 hubei/yunnan 一致的回落模式）。"""
    html_no_date = DETAIL_BODY_HTML.replace("发布日期：2026-08-15 10:30", " ")
    item = {"id": "x1", "col_code": "29", "title": "t",
            "date": "2026-08-10", "publish_dt": datetime(2026, 8, 10)}
    p = cs.build_payload(item, html_no_date)
    assert p["publish_time"] == datetime(2026, 8, 10)
    item2 = {"id": "x2", "col_code": "29", "title": "t", "date": "", "publish_dt": None}
    assert cs.build_payload(item2, html_no_date)["publish_time"] is None


def test_to_signed_simhash_matches_ingestor_convention():
    """uint64 simhash 归一为 int64 有符号（与 tender_ingestor 一致）。"""
    assert cs._to_signed_simhash(0x7FFFFFFFFFFFFFFF) == 0x7FFFFFFFFFFFFFFF
    assert cs._to_signed_simhash(0x8000000000000000) == -0x8000000000000000
    assert cs._to_signed_simhash(0xFFFFFFFFFFFFFFFF) == -1


# ---------------------------------------------------------------- 分页凑数（--pages）

@pytest.mark.asyncio
async def test_collect_pages_dedup_and_stop_at_limit():
    """多页拉取：跨页 id 去重，凑满 limit 后不再拉后续页（当页内多出项保留）。"""
    page1 = [{"id": "a"}, {"id": "b"}]
    page2 = [{"id": "b"}, {"id": "c"}, {"id": "d"}]
    calls = []

    async def fetch_page(pg):
        calls.append(pg)
        return page1 if pg == 1 else page2

    items = await cs._collect_pages(fetch_page, pages=3, limit=3)
    assert [it["id"] for it in items] == ["a", "b", "c", "d"]  # 页内多出项保留
    assert calls == [1, 2]  # 凑满即停，未拉第 3 页


@pytest.mark.asyncio
async def test_collect_pages_empty_page_stops():
    """某页拉空即停，不继续往后翻。"""
    calls = []

    async def fetch_page(pg):
        calls.append(pg)
        return [{"id": "a"}] if pg == 1 else []

    items = await cs._collect_pages(fetch_page, pages=5, limit=10)
    assert [it["id"] for it in items] == ["a"]
    assert calls == [1, 2]


# ---------------------------------------------------------------- 403 即停

class _FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _FakeClient:
    def __init__(self, status):
        self._status = status

    async def get(self, url):
        return _FakeResp(self._status)

    async def post(self, url, json=None, headers=None):
        return _FakeResp(self._status)


@pytest.mark.asyncio
async def test_fetch_403_raises_collect403():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)
    with pytest.raises(cs.Collect403):
        await cs._fetch(_FakeClient(403), "https://www.ccgp-shandong.gov.cn/x")


@pytest.mark.asyncio
async def test_fetch_200_returns_text():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)

    class _OkClient(_FakeClient):
        async def get(self, url):
            return _FakeResp(200, "<html>ok</html>")

    assert await cs._fetch(_OkClient(200), "https://x/y") == "<html>ok</html>"


@pytest.mark.asyncio
async def test_fetch_non200_raises_runtimeerror():
    """非 200 非 403（如 500）抛 RuntimeError，由调用方捕获后跳过不崩溃。"""
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)
    with pytest.raises(RuntimeError):
        await cs._fetch(_FakeClient(500), "https://www.ccgp-shandong.gov.cn/x")


@pytest.mark.asyncio
async def test_fetch_list_403_raises_collect403():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)
    with pytest.raises(cs.Collect403):
        await cs._fetch_list(_FakeClient(403))


@pytest.mark.asyncio
async def test_fetch_list_parses_contract_json():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)

    class _ListClient(_FakeClient):
        async def post(self, url, json=None, headers=None):
            return _FakeResp(200, _LIST_TEXT)

    items = await cs._fetch_list(_ListClient(200))
    assert len(items) == 2
    assert items[0]["id"] == "9f8e7d6c5b4a"


@pytest.mark.asyncio
async def test_fetch_list_non_json_returns_empty():
    """SPA 壳 HTML 兜底响应（非 JSON）→ 空列表，由调用方停止而非崩溃。"""
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)

    class _HtmlClient(_FakeClient):
        async def post(self, url, json=None, headers=None):
            return _FakeResp(200, "<!doctype html><html>spa shell</html>")

    assert await cs._fetch_list(_HtmlClient(200)) == []


@pytest.mark.asyncio
async def test_fetch_list_non200_raises_runtimeerror():
    """列表接口非 200 非 403 → RuntimeError，由调用方停止而非崩溃。"""
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)
    with pytest.raises(RuntimeError):
        await cs._fetch_list(_FakeClient(500))


def test_main_collect403_exits_one(monkeypatch):
    """CLI 入口：采集过程触发 403 合规停止 → exit code 1。"""

    async def _raise403(args):
        raise cs.Collect403("403 即停")

    monkeypatch.setattr(sys, "argv", ["collect_shandong.py", "--limit", "1"])
    monkeypatch.setattr(cs, "_main_async", _raise403)
    with pytest.raises(SystemExit) as ei:
        cs.main()
    assert ei.value.code == 1


def test_main_parses_args_and_runs(monkeypatch):
    """CLI 入口：参数解析后正常调用 _main_async（含 --pages/--dry-run）。"""
    seen = {}

    async def _record(args):
        seen["args"] = args

    monkeypatch.setattr(sys, "argv", ["collect_shandong.py", "--limit", "3",
                                       "--pages", "2", "--dry-run", "--all-types"])
    monkeypatch.setattr(cs, "_main_async", _record)
    cs.main()
    a = seen["args"]
    assert (a.limit, a.pages, a.dry_run, a.all_types) == (3, 2, True, True)
    assert a.interval == 8.0  # 合规默认间隔


# ---------------------------------------------------------------- 主流程拆分函数（D7 覆盖率整改）

def test_filter_result_items_default_and_all_types():
    """标题过滤：默认只留中标/成交/结果；all_types 不过滤。"""
    items = [
        {"id": "1", "col_code": "0302", "title": "某项目中标公告"},
        {"id": "2", "col_code": "0302", "title": "某项目采购需求征求意见"},
        {"id": "3", "col_code": "0302", "title": "某项目成交公告"},
    ]
    assert [it["id"] for it in cs.filter_result_items(items)] == ["1", "3"]
    assert len(cs.filter_result_items(items, all_types=True)) == 3


def _mk_item(nid: str, title: str = "某项目中标公告") -> dict:
    return {"id": nid, "col_code": "0302", "title": title,
            "date": "2026-08-15", "publish_dt": None}


class _DetailRouter:
    """按详情 URL 中 id 返回不同响应的假 client（覆盖四类分支）。"""

    def __init__(self):
        self.ok_json = json.dumps({"noticeBody": DETAIL_BODY_HTML}, ensure_ascii=False)
        self.empty_json = json.dumps({"noticeBody": ""}, ensure_ascii=False)

    async def get(self, url):
        if "id=transport" in url:
            raise httpx.ConnectError("boom")
        if "id=server500" in url:
            return _FakeResp(500)
        if "id=emptybody" in url:
            return _FakeResp(200, self.empty_json)
        return _FakeResp(200, self.ok_json)


@pytest.mark.asyncio
async def test_collect_detail_payloads_skip_branches():
    """详情循环四类分支：成功入库 + 连接失败/非200/无正文均跳过不阻断。"""
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)
    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn:8087", 0)
    items = [_mk_item("ok"), _mk_item("transport"),
             _mk_item("server500"), _mk_item("emptybody")]
    results = await cs.collect_detail_payloads(_DetailRouter(), items)
    assert len(results) == 1
    assert results[0]["project_name"] == "某项目中标公告"
    assert results[0]["source_platform"] == "shandong"


@pytest.mark.asyncio
async def test_main_async_robots_forbidden_exits(monkeypatch):
    """robots 禁止采集 → SystemExit（合规硬闸门）。"""

    async def _forbidden(url, ua):
        return False

    monkeypatch.setattr(cs.robots_checker, "is_allowed", _forbidden)
    args = types.SimpleNamespace(limit=1, pages=1, interval=0,
                                 dry_run=True, all_types=False)
    with pytest.raises(SystemExit):
        await cs._main_async(args)


@pytest.mark.asyncio
async def test_main_async_empty_list_stops(monkeypatch):
    """列表为空 → 打印契约变化警告并停止，不进详情环节。"""

    async def _allowed(url, ua):
        return True

    async def _empty_list(client, page=1, page_size=20):
        return []

    monkeypatch.setattr(cs.robots_checker, "is_allowed", _allowed)
    monkeypatch.setattr(cs, "_fetch_list", _empty_list)
    args = types.SimpleNamespace(limit=1, pages=1, interval=0,
                                 dry_run=True, all_types=False)
    await cs._main_async(args)  # 不抛异常即通过


@pytest.mark.asyncio
async def test_main_async_dry_run_full_flow(monkeypatch):
    """dry-run 全链路：列表→标题过滤→详情→不入库（注入假拉取层）。"""
    called = {}

    async def _allowed(url, ua):
        return True

    async def _fake_list(client, page=1, page_size=20):
        return [_mk_item("a", "某设备中标公告"),
                _mk_item("b", "征求意见公告")]

    async def _fake_details(client, items):
        called["items"] = items
        return [{"source_url": "u1"}]

    monkeypatch.setattr(cs.robots_checker, "is_allowed", _allowed)
    monkeypatch.setattr(cs, "_fetch_list", _fake_list)
    monkeypatch.setattr(cs, "collect_detail_payloads", _fake_details)
    args = types.SimpleNamespace(limit=5, pages=1, interval=0,
                                 dry_run=True, all_types=False)
    await cs._main_async(args)
    # 征求意见被标题过滤，仅中标公告进详情环节
    assert [it["id"] for it in called["items"]] == ["a"]


@pytest.mark.asyncio
async def test_persist_results_add_then_skip_duplicate():
    """入库去重：首次新增，同 source_url 二次入库全部跳过。"""
    payload = cs.build_payload(_mk_item("dup1"), DETAIL_BODY_HTML)
    added, skipped = await cs.persist_results([payload])
    assert (added, skipped) == (1, 0)
    added2, skipped2 = await cs.persist_results([payload])
    assert (added2, skipped2) == (0, 1)


# ---------------------------------------------------------------- 边界用例（D7 补全）

def test_maybe_b64_decode_invalid_base64_returns_original():
    """形似 base64 但长度非法（decode 抛错）→ 原样返回不崩溃。"""
    v = "A" * 17  # 非 4 的倍数，validate=True 会抛 binascii.Error
    assert cs._maybe_b64_decode(v) == v


def test_maybe_b64_decode_gb18030_fallback_for_gbk_bytes():
    """无 charset 声明的 GBK 字节：utf-8 严格解失败 → gb18030 兑底解出中文。

    注：gb18030 可严格解任意字节序列，第 188 行 replace 兑底为防御性死代码
    （仅理论上的 LookupError 双发才可达），不强测。
    """
    import base64 as b64
    raw = "<p>中标供应商</p>".encode("gbk")
    v = b64.b64encode(raw).decode("ascii")
    out = cs._maybe_b64_decode(v)
    assert "中标供应商" in out


def test_maybe_b64_decode_unknown_charset_declared():
    """声明了未知 codec（LookupError）→ 跳过该声明，回退 utf-8/gb18030 链。"""
    import base64 as b64
    raw = '<meta charset="no-such-codec-xyz"><p>ok</p>'.encode("utf-8")
    v = b64.b64encode(raw).decode("ascii")
    out = cs._maybe_b64_decode(v)
    assert "<p>ok</p>" in out


def test_parse_detail_html_json_non_dict_returns_text():
    """JSON 但非 dict（如纯数字）→ 原样返回，不抛错。"""
    assert cs.parse_detail_html("123") == "123"


def test_parse_detail_html_content_result_extra_nodes():
    """content/result 额外候选节点也能取到正文。"""
    text = json.dumps({"content": {"body": "<p>x</p>"}}, ensure_ascii=False)
    assert cs.parse_detail_html(text) == "<p>x</p>"


def test_sd_amount_wan_and_yi_units():
    """山东 fallback 金额：万元/亿元单位换算。"""
    assert cs.sd_extract_win_amount("中标（成交）金额 1.5 万元") == Decimal("15000")
    assert cs.sd_extract_win_amount("中标（成交）金额 2 亿元") == Decimal("200000000")


def test_sd_amount_invalid_number_returns_none():
    """金额串去千分位后非合法 Decimal → 返回 None（宁缺勿造）。"""
    assert cs.sd_extract_win_amount("中标（成交）金额 ,,, 元") is None


def test_parse_date_invalid_date_returns_none():
    """形似日期但月日非法（strptime 全失败）→ None。"""
    assert cs._parse_date("2026-13-45") is None
    assert cs._parse_date("无日期文本") is None
