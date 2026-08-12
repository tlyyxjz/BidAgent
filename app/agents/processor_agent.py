"""Agent 3: 数据加工 Agent。

职责：字段对齐、分类标注、相关性评分。

核心能力：
- 字段对齐（不同平台字段名映射到统一 schema）
- 分类标注（IT/工程/医疗等品类标注）
- 相关性评分（基于用户查询的 TF-IDF 相似度）

复用：app/processors/tender_ingestor.py + app/processors/tender_utils.py
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.processor")


# 停用词：地区/时间/虚词，拆分时去掉
# P0 修复：移除"招标"/"中标"/"成交"/"采购"等行业词，这些词应作为搜索词保留
# （用户查"招标"时，"招标"是有效的搜索意图，不应被过滤掉）
_STOP_WORDS = {
    "北京", "上海", "广东", "深圳", "浙江", "江苏", "四川", "湖北", "山东",
    "河南", "福建", "安徽", "天津", "重庆", "湖南", "河北", "江西", "广西",
    "云南", "贵州", "陕西", "甘肃", "青海", "宁夏", "新疆", "西藏", "海南",
    "内蒙古", "黑龙江", "吉林", "辽宁", "山西",
    "最近", "的", "公告", "系统", "信息",
    "政府", "人民政府",  # "政府"单独无意义，几乎所有公告都含
    "30天", "7天", "15天", "5天", "1天", "3天", "三个月", "一个月",
    "30d", "7d", "15d", "5d", "1d", "3d", "90d", "最近30天", "最近7天", "最近15天", "最近5天", "最近3天", "最近1天", "最近三个月", "最近一个月",
    "天", "月", "年", "最近", "本期",
    # 无意义组合词（用户查"所有的标"等场景）
    "所有", "的标", "所有的", "所有的标", "全部", "全部的", "全部的标",
    "所有项目", "全部项目", "所有公告", "全部公告",
    "所有中标", "全部中标", "所有招标", "全部招标",
}

# 业务领域词典：查询里出现这些词时优先保留（不拆分）
_DOMAIN_WORDS = {
    "医疗", "教育", "格力", "空调", "电脑", "软件", "云服务",
    "安保", "保安", "保洁", "物业", "装修", "工程", "建筑", "绿化",
    "家具", "印刷", "车辆", "电梯", "消防", "网络", "服务器",
}


def _split_topic(text: str) -> list[str]:
    """把整句 topic 拆成有意义的短词用于 LIKE 匹配。

    例："北京教育系统的中标公告 最近30天" -> ["教育"]
        "医疗设备采购" -> ["医疗", "设备"]
        "格力公司" -> ["格力"]

    策略：
    1. 按空格/标点切分
    2. 对每个片段，先剥离地区/通用前后缀
    3. 剩余部分若2-6字直接用；更长则按领域词提取
    4. 过滤停用词和单字
    """
    if not text:
        return []
    words: list[str] = []
    # 地级市名（2-3字），剥离时需要保留作为搜索词
    _CITY_NAMES = {
        "台州", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华",
        "衢州", "舟山", "丽水", "深圳", "青岛", "大连", "厦门", "苏州",
        "无锡", "南京", "成都", "武汉", "长沙", "郑州", "西安", "沈阳",
        "长春", "哈尔滨", "济南", "太原", "福州", "南昌", "广州", "昆明",
        "贵阳", "海口", "兰州", "西宁", "石家庄", "呼和浩特", "乌鲁木齐",
        "拉萨", "银川", "南宁",
    }
    parts = re.split(r"[\s,，。；;、（）()【】\[\]]+", text)
    for part in parts:
        part = part.strip()
        if not part or part in _STOP_WORDS:
            continue

        # 剥离常见前缀（地区词：省份 + 地级市 + 行政后缀）
        cleaned = part
        changed = True
        while changed:
            changed = False
            for prefix in ("北京", "上海", "广东", "深圳", "浙江", "江苏",
                           "四川", "湖北", "山东", "河南", "天津", "重庆",
                           "福建", "安徽", "湖南", "江西", "辽宁", "吉林",
                           "黑龙江", "山西", "陕西", "甘肃", "青海", "云南",
                           "贵州", "海南", "河北", "内蒙古", "新疆", "西藏",
                           "宁夏", "广西", "台州", "杭州", "宁波", "温州",
                           "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山",
                           "丽水"):
                if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 1:
                    # P0 修复：地级市名保留为搜索词（如"台州"）
                    if prefix in _CITY_NAMES and prefix not in words:
                        words.append(prefix)
                    cleaned = cleaned[len(prefix):]
                    changed = True
            # 剥离"省"/"市"/"区"/"县"行政后缀前缀
            if cleaned.startswith("省") and len(cleaned) > 2:
                cleaned = cleaned[1:]
                changed = True
            if cleaned.startswith("市") and len(cleaned) > 2:
                cleaned = cleaned[1:]
                changed = True

        # 剥离常见后缀（while 循环：每次剥离后重新遍历，避免连续过度剥离）
        changed_suffix = True
        while changed_suffix:
            changed_suffix = False
            for suffix in ("政府采购", "政府", "系统", "公告", "招标", "采购",
                           "项目", "信息", "中标", "成交", "公司", "集团",
                           "管理局", "委员会", "中心", "办公室"):
                if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
                    cleaned = cleaned[: -len(suffix)]
                    changed_suffix = True
                    break  # 重新开始遍历，确保长后缀优先

        cleaned = cleaned.strip()
        if not cleaned or cleaned in _STOP_WORDS:
            continue

        # 领域词优先：如果 cleaned 包含领域词，直接用领域词
        found_domain = False
        for dw in _DOMAIN_WORDS:
            if dw in cleaned:
                if dw not in words:
                    words.append(dw)
                found_domain = True
        if found_domain:
            continue

        # 2-6字的剩余片段直接用
        if 2 <= len(cleaned) <= 6:
            if cleaned not in _STOP_WORDS and cleaned not in words:
                words.append(cleaned)
            continue

        # 原始片段2-3字且非停用词
        if 2 <= len(part) <= 3 and part not in _STOP_WORDS:
            if part not in words:
                words.append(part)

    return words


async def processor_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 3: 数据加工（字段对齐 + 分类标注 + 相关性评分）。

    输入 state:
        - collect_summary: dict — 采集结果（来自 collector_agent）
        - subscription_id: int — 订阅 ID

    输出 state（新增）:
        - process_summary: dict — 加工结果摘要
            - total_processed: int — 加工总数
            - category_distribution: dict — 品类分布
            - avg_relevance_score: float — 平均相关性评分
    """
    collect_summary = state.get("collect_summary") or {}
    sub_id = state.get("subscription_id")
    if sub_id is None:
        raise ValueError("state.subscription_id is required")

    logger.info(
        "processor_agent started sub_id={} total_collected={}",
        sub_id, collect_summary.get("total", 0),
    )

    # 字段对齐已由 tender_ingestor.ingest_scrape_result 完成
    # 这里做分类标注 + 相关性评分（基于已入库的 tenders）
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select

    # 从 parsed_filters 提取 topic/region/keywords 用于过滤
    parsed = state.get("parsed_filters")
    topic = getattr(parsed, "topic", "") if parsed else ""
    region = getattr(parsed, "region", "") if parsed else ""
    keywords = getattr(parsed, "keywords", []) if parsed else []

    # 构建关键词列表（topic + keywords + query 分词）
    # 修复：topic 可能是整句（如"北京教育系统的中标公告 最近30天"），
    # 直接用整句做 LIKE 匹配会查不到任何数据。需要拆成有意义的短词。
    search_words: list[str] = []
    if topic:
        search_words.extend(_split_topic(topic))
    search_words.extend(keywords)
    # 从 raw_query 提取额外关键词
    raw_query = getattr(parsed, "raw_query", "") if parsed else ""
    if raw_query:
        search_words.extend(_split_topic(raw_query))
    # 去重去空
    search_words = [w for w in dict.fromkeys(search_words) if w]

    # P0 修复：过滤无意义搜索词（如"所有的标"/"所有的"/"的标"等）
    # 当用户查询只含地区+通用词时（如"河南省所有的标"），"河南"被当作
    # region提取后又当作停用词从search_words过滤掉，剩下"所有的标"这类
    # 无意义词匹配0条公告。此时应清空search_words，只用region过滤。
    _MEANINGLESS_WORDS = {
        "所有", "的", "标", "的标", "所有的", "所有的标",
        "全部", "的全部", "全部的", "全部的标",
        "项目", "的项目", "所有项目", "全部项目",
        "公告", "的公告", "所有公告", "全部公告",
        # 保留"所有中标"/"全部中标"等无意义组合，但移除单独的"中标"/"招标"
        # （单独的"招标"/"中标"是有效搜索词，第一批P0已从_STOP_WORDS移除）
        "所有中标", "全部中标",
        "所有招标", "全部招标",
    }
    search_words = [w for w in search_words if w not in _MEANINGLESS_WORDS]
    if not search_words and region:
        logger.info(
            "search_words过滤后为空,改用region='{}'做查询", region,
        )

    async with AsyncSessionLocal() as db:
        # 查询数据库：按 topic/keywords 过滤（采集失败时 fallback 到库内已有数据）
        from sqlalchemy import or_, and_

        # 采集0条时 platforms_collected 可能为空，fallback 查全部平台
        platforms_collected = collect_summary.get("platforms_collected") or []
        if platforms_collected:
            query = select(Tender).where(
                Tender.source_platform.in_(platforms_collected)
            )
        else:
            query = select(Tender)

        # P0 修复：无搜索关键词时不退化为全表查询（避免返回不相关数据）
        # 但如果有 region，仍可用 region 查询（如"河南省所有的标"场景）
        if not search_words and not region:
            logger.warning("no search_words and no region, skip query to avoid irrelevant data")
            state["process_summary"] = {
                "total_processed": 0,
                "category_distribution": {},
                "avg_relevance_score": 0.0,
                "platforms_collected": platforms_collected or ["ccgp"],
            }
            state["tender_ids"] = []
            state["processed_tenders"] = []
            return state

        # 如果有搜索关键词，按 project_name 或 core_content 过滤
        # B4 修复：裸 ilike → safe_contains（转义 %/_ 特殊字符，硬约束）
        from app.scheduler.utils import safe_contains
        if search_words:
            keyword_filters = []
            for kw in search_words:
                if kw:
                    keyword_filters.append(
                        or_(
                            safe_contains(Tender.project_name, kw),
                            safe_contains(Tender.core_content, kw),
                        )
                    )
            if keyword_filters:
                query = query.where(and_(*keyword_filters) if len(keyword_filters) == 1
                                    else or_(*keyword_filters))

        # 按 region 过滤
        # 匹配 tender_org（采购人）+ location（地区）+ project_name（项目名）
        # 不匹配 core_content（正文含地区名不代表项目在该地区）
        region_relaxed = False
        region_no_match = False
        if region:
            query = query.where(
                or_(
                    safe_contains(Tender.tender_org, region),
                    safe_contains(Tender.location, region),
                    safe_contains(Tender.project_name, region),
                )
            )

        # 按公告类型过滤（notice_types 中英文兼容映射 → DB 英文值）
        # B5 修复：删除"采购"→tender映射（"采购"是通用词，中标公告也含"采购"）
        _nt_map = {"中标": "award", "成交": "award", "更正": "correction",
                   "变更": "correction", "招标": "tender"}
        _nt_vals: list[str] = []
        for nt in (getattr(parsed, "notice_types", []) or []):
            matched = False
            for k, v in _nt_map.items():
                if k in str(nt) or v == str(nt):
                    _nt_vals.append(v)
                    matched = True
                    break
            if not matched:
                _nt_vals.append(str(nt))
        if _nt_vals:
            query = query.where(Tender.notice_type.in_(_nt_vals))

        # 加 time_range 过滤（用户说"最近7天"应只返回7天内公告）
        time_range = getattr(parsed, "time_range", None) or getattr(parsed, "date_range", None)
        time_days = _parse_time_range_days(time_range)
        if time_days:
            from datetime import datetime as _dt, timedelta as _td
            _cutoff = _dt.now() - _td(days=time_days)
            query = query.where(Tender.publish_time >= _cutoff)
            logger.info("time_range='{}' -> 过滤最近{}天 (cutoff={})", time_range, time_days, _cutoff.date())

        # F1 修复：排序改 publish_time desc（原 id desc，补录数据时 id 与时间不一致）
        result = await db.execute(
            query.order_by(Tender.publish_time.desc().nullslast()).limit(100)
        )
        tenders = result.scalars().all()

        # P3 修复：region 过滤后0条时不回退全国（避免不相关公告混入）
        # 原逻辑回退全国导致"浙江中标"返回上海/复旦等非浙江公告，准度0%
        # 新逻辑：保持0条结果，在 summary 标注 region_no_match 让前端提示用户
        if region and len(tenders) == 0:
            logger.warning(
                "region='{}' 过滤后0条，不回退全国（P3修复：保持结果准确性）",
                region,
            )
            region_no_match = True

    # 把查询到的 tender_ids 存入 state，供 delivery_agent 复用（避免
    # delivery 用不同过滤条件重查导致查到 0 条而不生成报告）
    state["tender_ids"] = [t.id for t in tenders]

    # 分类标注 + 相关性评分
    category_dist: dict[str, int] = {}
    relevance_scores: list[float] = []

    for t in tenders:
        # 分类标注（基于 project_name 关键词匹配）
        category = _classify_category(t.project_name or "")
        category_dist[category] = category_dist.get(category, 0) + 1

        # 相关性评分（简单 TF 匹配，MVP 阶段）
        score = _compute_relevance(t.project_name or "", t.core_content or "", topic)
        # B1 修复：把 score 挂到 tender 上，供后续 LLM 抽取按相关性排序
        t._relevance_score = score
        relevance_scores.append(score)

    # B1 修复：LLM 抽取前按相关性降序排序（原按 id desc，可能抽取到最不相关的公告）
    tenders = sorted(tenders, key=lambda t: getattr(t, "_relevance_score", 0.0), reverse=True)

    avg_relevance = (
        sum(relevance_scores) / len(relevance_scores)
        if relevance_scores else 0.0
    )

    state["process_summary"] = {
        "total_processed": len(tenders),
        "category_distribution": category_dist,
        "avg_relevance_score": round(avg_relevance, 3),
        "region_relaxed": region_relaxed,
        "region_no_match": region_no_match,
    }

    # 真实 LLM 抽取 6 类核心字段（接 app/llm/extractor.py）
    # 修复：原实现只做 SQL + 字符串匹配，核心卖点"LLM 抽取 6 类字段"未接入
    from app.llm.extractor import call_extraction_llm

    extracted_results: list[dict[str, Any]] = []
    llm_success = 0
    llm_failed = 0
    # P1 修复：从配置读取LLM抽取上限，避免硬编码
    from app.config import settings
    llm_max = getattr(settings, "LLM_EXTRACT_MAX", 10)  # 默认10条，可配置

    # P2 修复：并发抽取（Semaphore=3 限流，避免触发 API 限流）
    # 原实现串行抽取 10 条约需 200s，并发3后约 60-80s
    import asyncio as _asyncio
    _LLM_SEM = _asyncio.Semaphore(3)

    async def _extract_one(tender):
        raw_text = tender.source_raw_text or tender.core_content or ""
        if not raw_text.strip():
            return None, None
        async with _LLM_SEM:
            try:
                extraction = await call_extraction_llm(raw_text)
                return tender, extraction
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM 抽取失败 tender_id={} error={}", tender.id, str(exc))
                return tender, None

    # 筛选有文本的 tender，限制 llm_max 条
    _candidates = [
        t for t in tenders
        if (t.source_raw_text or t.core_content or "").strip()
    ][:llm_max]

    _results = await _asyncio.gather(
        *[_extract_one(t) for t in _candidates],
        return_exceptions=True,
    )

    for item in _results:
        if isinstance(item, Exception):
            llm_failed += 1
            continue
        t, extraction = item
        if extraction is None:
            llm_failed += 1
            continue
        raw_text = t.source_raw_text or t.core_content or ""
        extracted_results.append({
            "tender_id": t.id,
            "project_name": t.project_name,
            "source_url": t.source_url,
            "source_raw_text": raw_text,
            "fields": [f.model_dump() for f in extraction.fields],
            "model_id": extraction.model_id,
            "total_tokens": extraction.total_tokens,
            "latency_ms": extraction.latency_ms,
            "error": extraction.error,
        })
        if not extraction.error:
            llm_success += 1
        else:
            llm_failed += 1

    # A1+A2 修复：processed_tenders 存全量（含未抽取的 90 条），LLM 字段挂子集
    # 原实现只存 extracted_results（≤10 条），导致质检/金融分析覆盖不全
    _extracted_by_id = {r["tender_id"]: r for r in extracted_results}
    processed_tenders_full: list[dict[str, Any]] = []
    for t in tenders:
        pt = {
            "id": t.id,
            "project_name": t.project_name,
            "source_url": t.source_url,
            "source_raw_text": t.source_raw_text or t.core_content or "",
            "core_content": t.core_content or "",
            "tender_org": t.tender_org or "",
            "publish_time": t.publish_time.isoformat() if t.publish_time else None,
            "notice_type": t.notice_type or "",
            "source_platform": t.source_platform or "",
            "_relevance_score": getattr(t, "_relevance_score", 0.0),
        }
        ext = _extracted_by_id.get(t.id)
        if ext:
            pt["fields"] = ext["fields"]
            pt["model_id"] = ext.get("model_id")
            pt["total_tokens"] = ext.get("total_tokens")
            pt["llm_error"] = ext.get("error")
        else:
            pt["fields"] = []
        processed_tenders_full.append(pt)
    # 传递给 quality_agent 做证据定位 + 验证（全量数据）
    state["processed_tenders"] = processed_tenders_full
    state["process_summary"]["llm_extracted"] = llm_success
    state["process_summary"]["llm_failed"] = llm_failed

    logger.info(
        "processor_agent completed total_processed={} categories={} avg_relevance={:.3f} llm_ok={} llm_fail={}",
        len(tenders), len(category_dist), avg_relevance, llm_success, llm_failed,
    )
    return state


def _classify_category(project_name: str) -> str:
    """基于项目名关键词的简单分类标注。

    Args:
        project_name: 项目名称

    Returns:
        品类标签（IT / 工程 / 医疗 / 教育 / 其他）
    """
    name = project_name.lower()
    # 顺序：细分品类（医疗/教育）优先于通用品类（工程/IT），避免"教学楼建设"被归为工程
    if any(kw in name for kw in ["医疗", "医院", "器械", "药品"]):
        return "医疗"
    if any(kw in name for kw in ["教育", "学校", "教学", "图书"]):
        return "教育"
    if any(kw in name for kw in ["电脑", "服务器", "网络", "软件", "系统", "it", "信息化"]):
        return "IT"
    if any(kw in name for kw in ["工程", "施工", "建设", "装修", "改造"]):
        return "工程"
    return "其他"


import re as _re_time

def _parse_time_range_days(time_range: str | None) -> int | None:
    """解析 time_range 字符串为天数。

    支持：
    - '7d' / '7天' / '最近7天' -> 7
    - '30d' / '30天' / '最近30天' / '最近1个月' / '1个月' -> 30
    - '3m' / '3个月' / '最近3个月' -> 90
    - '1y' / '1年' / '最近1年' -> 365
    - None / '' / 无法解析 -> None（不过滤）

    Args:
        time_range: 时间范围字符串

    Returns:
        天数（int）或 None
    """
    # 类型保护：非字符串直接返回 None（兼容 MagicMock 测试）
    if not isinstance(time_range, str):
        return None
    if not time_range or not time_range.strip():
        return None
    s = time_range.strip()
    # 优先匹配显式单位
    m = _re_time.search(r"(\d+)\s*(d|天|日)", s, _re_time.IGNORECASE)
    if m:
        return int(m.group(1))
    m = _re_time.search(r"(\d+)\s*(m|个月|月)", s, _re_time.IGNORECASE)
    if m:
        return int(m.group(1)) * 30
    m = _re_time.search(r"(\d+)\s*(y|年)", s, _re_time.IGNORECASE)
    if m:
        return int(m.group(1)) * 365
    # 中文"最近X天"格式
    m = _re_time.search(r"最近(\d+)天", s)
    if m:
        return int(m.group(1))
    # 纯数字默认按天
    m = _re_time.search(r"(\d+)", s)
    if m:
        return int(m.group(1))
    return None


def _compute_relevance(project_name: str, core_content: str, topic: str) -> float:
    """基于关键词匹配的简单相关性评分。

    Args:
        project_name: 项目名称
        core_content: 核心内容
        topic: 用户查询主题

    Returns:
        0.0-1.0 的相关性评分
    """
    if not topic:
        return 0.5  # 无主题时给中等评分
    text = (project_name + " " + core_content).lower()
    topic_lower = topic.lower()
    # 简单关键词匹配
    if topic_lower in text:
        return 1.0
    # P0 修复：原实现对字符串迭代产生单字符列表，导致几乎所有中文公告都匹配
    # （每个单字如"的"/"项"都命中）。改为用 _split_topic 分词后做词级匹配。
    topic_words = _split_topic(topic)
    if not topic_words:
        return 0.5
    matched = sum(1 for w in topic_words if w in text)
    return matched / len(topic_words)
