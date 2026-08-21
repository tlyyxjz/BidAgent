"""实时源适配器具体实现（D2 起逐源接入）。

每个适配器 = 薄壳：import 对应 scripts/collect_*.py 的纯函数与 _fetch
（自带 domain_rate_limiter 限流 + 403→Collect403），
组装成 build_payload 契约的 payloads 交给 BaseSourceAdapter 统一合规包装。
不复制任何解析逻辑。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx

from app.services.realtime_sources import (
    UA,
    BaseSourceAdapter,
    SourceBlockedError,
)
from app.utils.logger import get_logger

logger = get_logger("services.realtime_adapters")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

# 与脚本一致的中标类标题过滤（宁可少给，不可把非结果类公告当中标公告入库）
_AWARD_TITLE_KEYS = ("中标", "成交", "结果")


def _load_script_module(name: str):
    """按文件名加载 scripts/ 下的采集器模块（scripts 非包，走 spec 加载）。"""
    path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scripts_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"无法加载采集器脚本: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HubeiAdapter(BaseSourceAdapter):
    """湖北站：首页列表 HTML → 详情 HTML → build_payload。"""

    source = "hubei"
    display_name = "湖北政府采购网"
    domain = "www.ccgp-hubei.gov.cn"
    list_url = "https://www.ccgp-hubei.gov.cn/"
    robots_entry_url = list_url

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        ch = _load_script_module("collect_hubei")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # D4 真机：禁用系统代理（httpx 默认读环境变量，
                # 本机代理会导致间歇性 ReadTimeout）
                trust_env=False,
                timeout=20.0,
            ) as client:
                home_html = await ch._fetch(client, self.list_url)
                items = ch.parse_list(home_html)
                # D4 真机修复：与脚本默认行为一致，只保留中标/成交/结果类公告
                # （过滤后才取 limit，避免名额被非中标类占掉）
                items = [it for it in items
                         if any(k in it.get("title", "") for k in _AWARD_TITLE_KEYS)]
                items = items[:limit]
                payloads: list[dict[str, Any]] = []
                for it in items:
                    url = it.get("url") or (
                        self.list_url.rstrip("/") + it.get("path", "")
                    )
                    # build_payload 依赖 item["url"]（脚本中由 _main_async 注入，
                    # 适配器需同样补齐），不修改原 item
                    it = {**it, "url": url}
                    detail_html = await ch._fetch(client, url)
                    payloads.append(ch.build_payload(it, detail_html))
                return payloads
        except ch.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc


class JiangsuAdapter(BaseSourceAdapter):
    """江苏站：首页链接 → 公告接口 JSON → parse_notice_payload。"""

    source = "jiangsu"
    display_name = "江苏政府采购网"
    domain = "www.ccgp-jiangsu.gov.cn"
    list_url = "http://www.ccgp-jiangsu.gov.cn/"
    robots_entry_url = list_url

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        cj = _load_script_module("collect_jiangsu")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # D4 真机：禁用系统代理（httpx 默认读环境变量，
                # 本机代理会导致间歇性 ReadTimeout）
                trust_env=False,
                timeout=20.0,
            ) as client:
                home = await cj._fetch(client, self.list_url)
                links = cj.parse_homepage_links(home)
                # D4 真机修复：首页头部多为非中标类公告，过滤后命中的
                # 才算数 → 多抓 limit+2 条候选（含限流约 8s/条，
                # 仍在适配器 45s 超时内），命中够数即停
                payloads: list[dict[str, Any]] = []
                for link in links[: limit + 2]:
                    api_url = (f"{cj.API}?gglb={link['gglb']}"
                               f"&ggid={link['ggid']}&projId=")
                    raw = await cj._fetch(client, api_url)
                    payload = json.loads(raw.lstrip("\ufeff"))
                    if payload.get("msg") != "OK":
                        # 接口异常单条跳过（宁可少给，不可编造）
                        logger.warning("jiangsu api not OK ggid={}",
                                       link.get("ggid"))
                        continue
                    p = cj.parse_notice_payload(payload)
                    p["source_url"] = link["url"]
                    # D4 真机修复：首页链接混有多类型公告，只入库中标类
                    # （列表阶段无标题，只能在解析后过滤；
                    #   title 与 projName 都查，避免 fallback 标题误伤）
                    _jn_data = (payload.get("data") or [{}])
                    _jn_title = str(_jn_data[0].get("title") or "") + str(
                        (payload.get("cgxm") or {}).get("projName") or "")
                    if not any(k in _jn_title for k in _AWARD_TITLE_KEYS):
                        continue
                    payloads.append(p)
                    if len(payloads) >= limit:
                        break
                return payloads
        except cj.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc


class YunnanAdapter(BaseSourceAdapter):
    """云南站：gghtlist 接口（POST）→ 详情 HTML → build_payload。"""

    source = "yunnan"
    display_name = "云南政府采购网"
    domain = "www.ccgp-yunnan.gov.cn"
    list_url = "http://www.ccgp-yunnan.gov.cn/"
    robots_entry_url = list_url

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        cy = _load_script_module("collect_yunnan")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # D4 真机：禁用系统代理（httpx 默认读环境变量，
                # 本机代理会导致间歇性 ReadTimeout）
                trust_env=False,
                timeout=25.0,
            ) as client:
                items = await cy._fetch_list(client)
                if not items:
                    # D4 真机实测：接口偶发 HTTP 200 + 体内 status=500
                    # "系统异常"（解析为空列表），隔几秒重试即恢复；
                    # 最多重试一次，限流器保证两次请求间隔合规
                    logger.warning("yunnan list empty, retry once")
                    items = await cy._fetch_list(client)
                items = [it for it in items
                         if any(k in it["title"] for k in _AWARD_TITLE_KEYS)]
                payloads: list[dict[str, Any]] = []
                for it in items[:limit]:
                    url = (self.list_url.rstrip("/")
                           + cy.DETAIL_PATH.format(bid=it["bulletin_id"]))
                    try:
                        detail_html = await cy._fetch(client, url)
                    except httpx.TransportError as exc:
                        # 单条失败降级：跳过该条，不阻断整源
                        logger.warning("yunnan detail failed bid={} err={}",
                                       it["bulletin_id"], exc)
                        continue
                    payloads.append(cy.build_payload(it, detail_html))
                return payloads
        except cy.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc


class ShandongAdapter(BaseSourceAdapter):
    """山东站：getListByCode（POST）→ getDetail → build_payload。

    D6 实机核对：主站 :443 对 API 路径返 405，API 已搬到
    https://www.ccgp-shandong.gov.cn:8087/api（bundle 内 axios baseURL）；
    colCode=0302 结果公告；详情用 getDetail（getNoticeBody 对结果公告返
    “公告正文不存在”），body 为 base64 GBK HTML（collect_shandong 内解）。
    响应双层包装（collect_shandong._api_body 解包）；接口偶发 code=999
    限流抖动 → 跳过该条不阻断。:8087 证书对 www 域不可验证，verify=False。
    """

    source = "shandong"
    display_name = "山东政府采购网"
    domain = "www.ccgp-shandong.gov.cn"
    list_url = "https://www.ccgp-shandong.gov.cn/"
    robots_entry_url = list_url

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        cs = _load_script_module("collect_shandong")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # D4 真机：禁用系统代理（httpx 默认读环境变量，
                # 本机代理会导致间歇性 ReadTimeout）；
                # D6：:8087 证书链对 www 域不可验证，verify=False
                trust_env=False,
                verify=False,
                timeout=25.0,
            ) as client:
                items = await cs._fetch_list(client, page_size=max(limit, 20))
                items = [it for it in items
                         if any(k in it["title"] for k in _AWARD_TITLE_KEYS)]
                payloads: list[dict[str, Any]] = []
                for it in items[:limit]:
                    url = cs.API_BASE + cs.DETAIL_API.format(
                        id=it["id"], code=it["col_code"])
                    try:
                        detail_html = cs.parse_detail_html(
                            await cs._fetch(client, url))
                    except httpx.TransportError as exc:
                        logger.warning("shandong detail failed id={} err={}",
                                       it["id"], exc)
                        continue
                    except RuntimeError as exc:
                        # 接口偶发 code=999/5xx（限流抖动）：跳过该条不阻断
                        logger.warning("shandong detail api-error id={} err={}",
                                       it["id"], exc)
                        continue
                    if "<" not in detail_html:
                        logger.warning("shandong detail empty id={}", it["id"])
                        continue
                    payloads.append(cs.build_payload(it, detail_html))
                return payloads
        except cs.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc

class TianjinAdapter(BaseSourceAdapter):
    """天津站：首页 HTML 列表 → 详情 HTML → build_payload（15城·首城）。

    首页即含中标类公告直链（SSR 门户），无需列表 API。
    同域 8s 限流下每条约 16s（列表+详情），超时放宽到 300s。
    """

    source = "tianjin"
    display_name = "天津市政府采购网"
    domain = "www.ccgp-tianjin.gov.cn"
    list_url = "https://www.ccgp-tianjin.gov.cn/"
    robots_entry_url = list_url
    timeout_seconds = 300.0

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        ct = _load_script_module("collect_tianjin")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # 与既有适配器一致：禁用系统代理（间歇性 ReadTimeout）
                trust_env=False,
                timeout=20.0,
            ) as client:
                home_html = await ct._fetch(client, self.list_url)
                items = ct.parse_list(home_html)
                items = ct.filter_result_items(items)
                payloads: list[dict[str, Any]] = []
                for it in items[:limit]:
                    try:
                        detail_html = await ct._fetch(client, it["url"])
                    except httpx.TransportError as exc:
                        logger.warning("tianjin detail failed id={} err={}",
                                       it["id"], exc)
                        continue
                    except RuntimeError as exc:
                        # 单条详情非 200：跳过不阻断整源（与山东/云南一致）
                        logger.warning("tianjin detail http-error id={} err={}",
                                       it["id"], exc)
                        continue
                    p = ct.build_payload(it, detail_html)
                    # 诚实原则：编号与中标人都抽不到的不入中标库
                    if not p["bid_number"] and not p["win_company"]:
                        continue
                    payloads.append(p)
                return payloads
        except ct.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc


class QingdaoAdapter(BaseSourceAdapter):
    """青岛站：列表 API（site-info/page，colCode=0304 结果公告）元数据模式。

    详情正文走交易系统附件渠道不对公网开放，列表接口提供标题/编号/
    发布时间/区域等元数据，如实入库并在 core_content 注明口径。
    """

    source = "qingdao"
    display_name = "青岛市政府采购网"
    domain = "zfcg.qingdao.gov.cn"
    list_url = "http://www.ccgp-qingdao.gov.cn/"
    robots_entry_url = list_url
    timeout_seconds = 120.0

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        cq = _load_script_module("collect_qingdao")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # 与既有适配器一致：禁用系统代理（间歇性 ReadTimeout）
                trust_env=False,
                timeout=20.0,
            ) as client:
                records = await cq.fetch_records(client, page=1,
                                                 limit=max(limit, 10))
                items = cq.filter_result_items(records)
                payloads: list[dict[str, Any]] = []
                for rec in items[:limit]:
                    # 诚实原则：列表无编号且无正文渠道的不入中标库
                    if not rec.get("project_code"):
                        continue
                    payloads.append(cq.build_payload(rec))
                return payloads
        except cq.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc


class HenanAdapter(BaseSourceAdapter):
    """河南省站：首页 SSR 公告区块 → 详情页核验（省级批量·第一站）。

    公告正文为 PDF 附件渠道，公开页面仅提供元数据 → 列表元数据模式：
    win_amount/win_company/tender_org 置 None，core_content 注明口径。
    同域 8s 限流下每条需列表+详情两次请求，超时放宽到 300s。
    """

    source = "henan"
    display_name = "河南省政府采购网"
    domain = "www.ccgp-henan.gov.cn"
    list_url = "http://www.ccgp-henan.gov.cn/"
    robots_entry_url = list_url
    timeout_seconds = 300.0

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        ch = _load_script_module("collect_henan")
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA}, follow_redirects=True,
                # 与既有适配器一致：禁用系统代理（间歇性 ReadTimeout）
                trust_env=False,
                timeout=20.0,
            ) as client:
                home_html = await ch.fetch_home(client)
                items = ch.filter_result_items(ch.parse_list(home_html))
                payloads: list[dict[str, Any]] = []
                for it in items[:limit]:
                    detail_html = await ch.fetch_detail(client, it["url"])
                    detail = ch.parse_detail(detail_html) if detail_html else None
                    payloads.append(ch.build_payload(it, detail))
                return payloads
        except ch.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc


class ZhejiangAdapter(BaseSourceAdapter):
    """浙江省站：阿里云 WAF JS 挑战 → 真实浏览器通道（Playwright）。

    httpx 直连只拿到挑战页，必须浏览器过挑战后在页面上下文调
    /portal/category（采购结果公告）与 /portal/detail（结构化模板 HTML，
    class 标记精确抽取中标人/金额）——完整抽取模式，非元数据降级。
    浏览器启动+挑战+逐条详情耗时较长，超时放宽到 360s。
    """

    source = "zhejiang"
    display_name = "浙江省政府采购网"
    domain = "www.ccgp-zhejiang.gov.cn"
    list_url = "http://www.ccgp-zhejiang.gov.cn/"
    robots_entry_url = list_url
    timeout_seconds = 360.0

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        ch = _load_script_module("collect_zhejiang")
        try:
            return await ch.collect_payloads(limit=limit, interval=2.0)
        except ch.CollectBlocked as exc:
            raise SourceBlockedError(str(exc)) from exc


class GuangdongAdapter(BaseSourceAdapter):
    """广东省站：gpcms REST 接口 + 浏览器通道（WAF 拒非浏览器 UA）。

    壳页纯 JS SPA，数据走 selectInfoMoreChannel（noticeType=00102
    结果公告）+ getInfoById（正文含"三、采购结果"表，中标人/金额
    完整可抽）——完整抽取模式。WAF 对自报爬虫 UA 返 403，故与浙江
    同走 Playwright 浏览器通道（正常浏览器标识 = 真人正常浏览）。
    浏览器启动+逐条详情耗时较长，超时放宽到 360s。
    """

    source = "guangdong"
    display_name = "广东省政府采购网"
    domain = "gdgpo.czt.gd.gov.cn"
    list_url = "https://gdgpo.czt.gd.gov.cn/"
    robots_entry_url = list_url
    timeout_seconds = 360.0

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        cg = _load_script_module("collect_guangdong")
        try:
            return await cg.collect_payloads(limit=limit)
        except cg.Collect403 as exc:
            raise SourceBlockedError(str(exc)) from exc
