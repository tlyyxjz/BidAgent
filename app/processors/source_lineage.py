"""来源谱系判定 (v4.1 第八章 8.1-8.3 节).

核心约束 (project_memory + v4.1):
- SimHash 只能提供候选, 不能单独决定来源谱系
- 转载识别使用多特征组合
- 同一公告的多个转载归入同一公告和来源谱系
- 同一项目的不同业务公告不得去重
- 更正公告与原公告建立替代关系, 不得删除
- 相似但无法确认的页面保留并标记待判断

判定结果 (v4.1 8.2):
- 明确原始页面 (official_original)
- 明确官方转载 (official_repost)
- 明确商业转载 (commercial_repost)
- 疑似同源 (same_origin)
- 独立性未知 (consistent_unknown)
- 确认独立来源 (independent)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.processors.simhash import compute_simhash, hamming_distance


RULE_VERSION = "source_lineage_v1.0"

# 来源角色 (与 display_grade.SOURCE_QUALITIES 一致)
SOURCE_ROLE_OFFICIAL_ORIGINAL = "official_original"
SOURCE_ROLE_OFFICIAL_REPOST = "official_repost"
SOURCE_ROLE_COMMERCIAL_REPOST = "commercial_repost"
SOURCE_ROLE_INDEX_ONLY = "index_only"
SOURCE_ROLE_UNKNOWN = "unknown"

SOURCE_ROLES = {
    SOURCE_ROLE_OFFICIAL_ORIGINAL,
    SOURCE_ROLE_OFFICIAL_REPOST,
    SOURCE_ROLE_COMMERCIAL_REPOST,
    SOURCE_ROLE_INDEX_ONLY,
    SOURCE_ROLE_UNKNOWN,
}

# 谱系状态 (与 display_grade.CROSS_VERIFY_STATUSES 一致)
LINEAGE_INDEPENDENT = "independent"
LINEAGE_SAME_ORIGIN = "same_origin"
LINEAGE_VERSION_DIFFERENCE = "version_difference"
LINEAGE_CONSISTENT_UNKNOWN = "consistent_unknown"
LINEAGE_CONFLICT = "conflict"
LINEAGE_SINGLE_SOURCE = "single_source"

LINEAGE_STATUSES = {
    LINEAGE_INDEPENDENT,
    LINEAGE_SAME_ORIGIN,
    LINEAGE_VERSION_DIFFERENCE,
    LINEAGE_CONSISTENT_UNKNOWN,
    LINEAGE_CONFLICT,
    LINEAGE_SINGLE_SOURCE,
}

# SimHash 汉明距离阈值 (SimHash 模块默认 3, 这里用 3 作为同源候选阈值)
SIMHASH_SAME_ORIGIN_THRESHOLD = 3

# 官方域名集合 (用于来源角色判定)
OFFICIAL_DOMAINS = {
    "ccgp.gov.cn",      # 中国政府采购网
    "ggzy.gov.cn",      # 公共资源交易网
    "ccgp.com.cn",
}

# 商业转载平台域名
COMMERCIAL_DOMAINS = {
    "qianlima.com",     # 千里马招标网
    "chinabidding.com.cn",
    "bidcenter.com.cn",
    "custos.com.cn",
}


@dataclass(frozen=True)
class SourceLineageFeatures:
    """来源谱系判定输入特征 (v4.1 8.1 节).

    十类识别特征:
    1. url: 原始链接
    2. page_source_label: 页面来源标注
    3. project_identifier: 项目编号
    4. notice_type: 公告类型
    5. title: 标题
    6. content_simhash: 正文 SimHash (None 表示未计算)
    7. publisher: 发布主体
    8. publish_time: 发布时间 (ISO 8601 字符串)
    9. attachment_urls: 附件链接列表
    10. upstream_source_mention: 正文中上游来源说明
    """

    url: str
    title: str
    notice_type: str = "other"
    project_identifier: Optional[str] = None
    page_source_label: Optional[str] = None
    content_simhash: Optional[int] = None
    publisher: Optional[str] = None
    publish_time: Optional[str] = None
    attachment_urls: list[str] = field(default_factory=list)
    upstream_source_mention: Optional[str] = None


def _extract_domain(url: str) -> str:
    """从 URL 提取主域名 (去掉 www 等前缀)."""
    if not url:
        return ""
    # 去掉协议
    cleaned = url.replace("https://", "").replace("http://", "")
    # 取路径前的部分
    domain = cleaned.split("/")[0]
    # 去掉端口
    domain = domain.split(":")[0]
    # 去掉 www. 前缀
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.lower()


def classify_source_role(features: SourceLineageFeatures) -> str:
    """判定来源角色 (v4.1 8.2 节).

    规则:
    1. 页面来源标注优先 (如果标注了上游来源, 说明是转载)
    2. 域名匹配官方域名 → official_original 或 official_repost
    3. 域名匹配商业域名 → commercial_repost
    4. 无附件且内容少 → index_only
    5. 无法判定 → unknown
    """
    domain = _extract_domain(features.url)

    # 规则 1: 页面来源标注
    if features.upstream_source_mention:
        # 如果正文提到上游来源, 且当前域名是官方域名, 则为官方转载
        if domain in OFFICIAL_DOMAINS:
            return SOURCE_ROLE_OFFICIAL_REPOST
        # 否则为商业转载
        return SOURCE_ROLE_COMMERCIAL_REPOST

    # 规则 2: 官方域名
    if domain in OFFICIAL_DOMAINS:
        # 官方域名 + 无上游来源标注 → 原始页面
        return SOURCE_ROLE_OFFICIAL_ORIGINAL

    # 规则 3: 商业域名
    if domain in COMMERCIAL_DOMAINS:
        return SOURCE_ROLE_COMMERCIAL_REPOST

    # 规则 4: 无附件且无项目编号 → 可能是索引页面
    if not features.attachment_urls and not features.project_identifier:
        return SOURCE_ROLE_INDEX_ONLY

    # 规则 5: 无法判定
    return SOURCE_ROLE_UNKNOWN


def detect_same_origin(
    features_a: SourceLineageFeatures,
    features_b: SourceLineageFeatures,
) -> tuple[str, float]:
    """检测两个来源是否同源 (v4.1 8.1-8.2 节).

    多特征组合判定:
    1. URL 完全相同 → same_origin (1.0)
    2. 项目编号相同 + 公告类型相同 + SimHash 距离≤3 → same_origin (0.9)
    3. SimHash 距离≤3 + 标题高度相似 → same_origin (0.8) [SimHash 只提供候选]
    4. 项目编号相同 + 公告类型不同 → version_difference (0.7)
    5. SimHash 距离≤3 但无其他佐证 → consistent_unknown (0.5)
    6. 无任何匹配特征 → independent (0.0)

    Returns:
        (状态, 置信度) — 状态为 LINEAGE_* 常量
    """
    # 规则 1: URL 完全相同
    if features_a.url and features_a.url == features_b.url:
        return LINEAGE_SAME_ORIGIN, 1.0

    # 规则 2: 项目编号相同 + 公告类型相同 + SimHash 距离≤3
    same_project = (
        features_a.project_identifier
        and features_b.project_identifier
        and features_a.project_identifier == features_b.project_identifier
    )
    same_notice_type = features_a.notice_type == features_b.notice_type

    simhash_close = False
    if features_a.content_simhash is not None and features_b.content_simhash is not None:
        dist = hamming_distance(features_a.content_simhash, features_b.content_simhash)
        simhash_close = dist <= SIMHASH_SAME_ORIGIN_THRESHOLD

    if same_project and same_notice_type and simhash_close:
        return LINEAGE_SAME_ORIGIN, 0.9

    # 规则 3: SimHash 距离≤3 + 标题高度相似
    title_similar = _title_similarity(features_a.title, features_b.title) >= 0.8
    if simhash_close and title_similar:
        return LINEAGE_SAME_ORIGIN, 0.8

    # 规则 4: 项目编号相同 + 公告类型不同 → 版本差异
    if same_project and not same_notice_type:
        return LINEAGE_VERSION_DIFFERENCE, 0.7

    # 规则 5: SimHash 距离≤3 但无其他佐证 → 独立性未知
    if simhash_close:
        return LINEAGE_CONSISTENT_UNKNOWN, 0.5

    # 规则 6: 无任何匹配特征
    return LINEAGE_INDEPENDENT, 0.0


def _title_similarity(title_a: str, title_b: str) -> float:
    """计算标题相似度 (Jaccard 字符级).

    Returns:
        0.0 - 1.0
    """
    if not title_a or not title_b:
        return 0.0
    set_a = set(title_a)
    set_b = set(title_b)
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def classify_independence(
    features_list: list[SourceLineageFeatures],
) -> str:
    """判定一组来源的独立性 (v4.1 8.2 节).

    Returns:
        - LINEAGE_SINGLE_SOURCE: 只有一个来源
        - LINEAGE_INDEPENDENT: 所有来源两两独立
        - LINEAGE_SAME_ORIGIN: 存在同源来源
        - LINEAGE_CONSISTENT_UNKNOWN: 存在独立性未知
    """
    if len(features_list) <= 1:
        return LINEAGE_SINGLE_SOURCE

    has_same_origin = False
    has_unknown = False

    for i, fa in enumerate(features_list):
        for fb in features_list[i + 1 :]:
            status, _ = detect_same_origin(fa, fb)
            if status == LINEAGE_SAME_ORIGIN:
                has_same_origin = True
            elif status == LINEAGE_CONSISTENT_UNKNOWN:
                has_unknown = True

    if has_same_origin:
        return LINEAGE_SAME_ORIGIN
    if has_unknown:
        return LINEAGE_CONSISTENT_UNKNOWN
    return LINEAGE_INDEPENDENT


def build_lineage_features(
    url: str,
    title: str,
    notice_type: str = "other",
    content_text: Optional[str] = None,
    project_identifier: Optional[str] = None,
    page_source_label: Optional[str] = None,
    publisher: Optional[str] = None,
    publish_time: Optional[str] = None,
    attachment_urls: Optional[list[str]] = None,
    upstream_source_mention: Optional[str] = None,
) -> SourceLineageFeatures:
    """便捷构造函数: 自动计算 SimHash.

    Args:
        content_text: 正文文本 (用于自动计算 SimHash, None 则不计算)
    """
    content_simhash = None
    if content_text:
        content_simhash = compute_simhash(content_text)

    return SourceLineageFeatures(
        url=url,
        title=title,
        notice_type=notice_type,
        project_identifier=project_identifier,
        page_source_label=page_source_label,
        content_simhash=content_simhash,
        publisher=publisher,
        publish_time=publish_time,
        attachment_urls=attachment_urls or [],
        upstream_source_mention=upstream_source_mention,
    )
