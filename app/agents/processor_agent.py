"""Agent 3: 数据加工 Agent。

职责：字段对齐、分类标注、相关性评分。

核心能力：
- 字段对齐（不同平台字段名映射到统一 schema）
- 分类标注（IT/工程/医疗等品类标注）
- 相关性评分（基于用户查询的 TF-IDF 相似度）

复用：app/processors/tender_ingestor.py + app/processors/tender_utils.py
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.processor")


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
    search_words = []
    if topic:
        search_words.append(topic)
    search_words.extend(keywords)
    # 从 raw_query 提取额外关键词
    raw_query = getattr(parsed, "raw_query", "") if parsed else ""
    if raw_query:
        # 去掉地区词后剩下的作为关键词
        region_words = ["北京", "上海", "广东", "深圳", "浙江", "江苏", "四川",
                        "湖北", "山东", "河南", "福建", "安徽", "最近", "的", "招标", "公告"]
        extra = [w for w in raw_query.replace("招标", "").replace("公告", "").split()
                 if w and w not in region_words and w not in search_words]
        search_words.extend(extra)

    async with AsyncSessionLocal() as db:
        # 查询数据库：按 topic/keywords 过滤（采集失败时 fallback 到库内已有数据）
        from sqlalchemy import or_, and_

        query = select(Tender).where(
            Tender.source_platform.in_(
                collect_summary.get("platforms_collected", ["ccgp"])
            )
        )

        # 如果有搜索关键词，按 project_name 或 core_content 过滤
        if search_words:
            keyword_filters = []
            for kw in search_words:
                if kw:
                    keyword_filters.append(
                        or_(
                            Tender.project_name.ilike(f"%{kw}%"),
                            Tender.core_content.ilike(f"%{kw}%"),
                        )
                    )
            if keyword_filters:
                query = query.where(and_(*keyword_filters) if len(keyword_filters) == 1
                                    else or_(*keyword_filters))

        # 按 region 过滤
        if region:
            query = query.where(
                or_(
                    Tender.tender_org.ilike(f"%{region}%"),
                    Tender.core_content.ilike(f"%{region}%"),
                )
            )

        result = await db.execute(
            query.order_by(Tender.id.desc()).limit(100)
        )
        tenders = result.scalars().all()

    # 分类标注 + 相关性评分
    category_dist: dict[str, int] = {}
    relevance_scores: list[float] = []

    for t in tenders:
        # 分类标注（基于 project_name 关键词匹配）
        category = _classify_category(t.project_name or "")
        category_dist[category] = category_dist.get(category, 0) + 1

        # 相关性评分（简单 TF 匹配，MVP 阶段）
        score = _compute_relevance(t.project_name or "", t.core_content or "", topic)
        relevance_scores.append(score)

    avg_relevance = (
        sum(relevance_scores) / len(relevance_scores)
        if relevance_scores else 0.0
    )

    state["process_summary"] = {
        "total_processed": len(tenders),
        "category_distribution": category_dist,
        "avg_relevance_score": round(avg_relevance, 3),
    }

    logger.info(
        "processor_agent completed total_processed={} categories={} avg_relevance={:.3f}",
        len(tenders), len(category_dist), avg_relevance,
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
    # 分词后部分匹配
    topic_words = [w for w in topic_lower if w.strip()]
    if not topic_words:
        return 0.5
    matched = sum(1 for w in topic_words if w in text)
    return matched / len(topic_words)
