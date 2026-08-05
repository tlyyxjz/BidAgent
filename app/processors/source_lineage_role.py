"""来源角色判定（从 source_lineage.py 拆分）。

对应总规划 v4.1 第八章「来源角色」。

包含：
1. 来源角色枚举常量（official_original / official_repost / commercial_repost /
   authorized_original / index_only / unknown）
2. 官方/商业域名关键词特征
3. judge_source_role 判定函数 + _extract_domain 辅助函数

工程约束：
- 来源角色判定基于 URL 域名 + 内容特征（不依赖 LLM）
- 纯函数，无副作用
"""
from __future__ import annotations

from urllib.parse import urlparse


# ========== 来源角色枚举（v4.1 第八章）==========

SOURCE_ROLE_OFFICIAL_ORIGINAL = "official_original"
SOURCE_ROLE_OFFICIAL_REPOST = "official_repost"
SOURCE_ROLE_COMMERCIAL_REPOST = "commercial_repost"
SOURCE_ROLE_AUTHORIZED_ORIGINAL = "authorized_original"
SOURCE_ROLE_INDEX_ONLY = "index_only"
SOURCE_ROLE_UNKNOWN = "unknown"

VALID_SOURCE_ROLES = (
    SOURCE_ROLE_OFFICIAL_ORIGINAL,
    SOURCE_ROLE_OFFICIAL_REPOST,
    SOURCE_ROLE_COMMERCIAL_REPOST,
    SOURCE_ROLE_AUTHORIZED_ORIGINAL,
    SOURCE_ROLE_INDEX_ONLY,
    SOURCE_ROLE_UNKNOWN,
)

# 官方域名特征（政府采购网、各省政府采购网、公共资源交易中心）
_OFFICIAL_DOMAIN_KEYWORDS = (
    "ccgp",        # 中央政府采购网
    "gov",         # 政府域名
    "gpo",         # 政府采购
    "ggzy",        # 公共资源交易
    "jczx",        # 交易中心
)

# 商业转载域名特征
_COMMERCIAL_DOMAIN_KEYWORDS = (
    "bidcenter",   # 中国采招网
    "chinabidding",  # 中国招标网
    "bidnews",     # 招标新闻
    "procurement",  # 采购网（非官方）
    "zhaobiao",    # 招标
)


# ========== 来源角色判定 ==========

def _extract_domain(url: str) -> str:
    """从 URL 提取主域名（小写，去除 www 前缀）。"""
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:  # noqa: BLE001
        return ""


def judge_source_role(
    source_url: str,
    *,
    content_text: str = "",
    is_original_publication: bool = False,
    is_authorized_original: bool = False,
    is_index_only: bool = False,
) -> tuple[str, str]:
    """判定页面来源角色。

    判定规则（v4.1 第八章，优先级从高到低）：
    1. is_original_publication=True → official_original（调用方明确标记为首发）
    2. 域名包含官方关键词 → official_original 或 official_repost
       - 若 content_text 中含"转载""来源："等标记 → official_repost
       - 否则 → official_original
    3. 域名包含商业关键词 → commercial_repost
    4. 其余 → unknown
    5. is_index_only=True → index_only（调用方标记为仅索引页）
    6. is_authorized_original=True 且商业域名 → authorized_original
    7. content_text 为空或极短（<50字符）且非官方域名 → index_only

    Args:
        source_url: 来源页面 URL
        content_text: 页面内容（用于检测转载标记）
        is_original_publication: 调用方明确标记为首发

    Returns:
        (source_role, reason)
    """
    if is_original_publication:
        return SOURCE_ROLE_OFFICIAL_ORIGINAL, "调用方标记为首发"

    # v4.1 §4.6: 仅索引页（调用方明确标记或正文为空且非官方）
    if is_index_only:
        return SOURCE_ROLE_INDEX_ONLY, "调用方标记为仅索引页"

    domain = _extract_domain(source_url)
    if not domain:
        return SOURCE_ROLE_UNKNOWN, "无法解析域名"

    # v4.1 §4.6: 正文基本为空（<10 字符，仅索引/导航文本），且非官方域名 → index_only
    # 注：阈值 10 区分“无正文”与“短但真实的公告正文”；v4.1 §4.6 index_only 语义为“仅提供索引链接，无正文”
    if (not content_text or len(content_text.strip()) < 10) and not is_authorized_original:
        # 检查是否是官方域名
        is_official = any(kw in domain for kw in _OFFICIAL_DOMAIN_KEYWORDS)
        if not is_official:
            return SOURCE_ROLE_INDEX_ONLY, f"正文基本为空且非官方域名({domain})"

    # 检测转载标记
    repost_markers = ("转载", "来源：", "文章来源", "信息来源", "转自")
    has_repost_marker = any(m in content_text for m in repost_markers) if content_text else False

    # 官方域名
    for kw in _OFFICIAL_DOMAIN_KEYWORDS:
        if kw in domain:
            if has_repost_marker:
                return SOURCE_ROLE_OFFICIAL_REPOST, f"官方域名({domain})且含转载标记"
            return SOURCE_ROLE_OFFICIAL_ORIGINAL, f"官方域名({domain})无转载标记"

    # 商业域名
    for kw in _COMMERCIAL_DOMAIN_KEYWORDS:
        if kw in domain:
            # v4.1 §4.6: 被授权的商业平台首发
            if is_authorized_original:
                return SOURCE_ROLE_AUTHORIZED_ORIGINAL, f"授权商业域名首发({domain})"
            return SOURCE_ROLE_COMMERCIAL_REPOST, f"商业域名({domain})"

    # 未知域名，但含转载标记
    if has_repost_marker:
        return SOURCE_ROLE_COMMERCIAL_REPOST, f"非官方域名({domain})且含转载标记"

    return SOURCE_ROLE_UNKNOWN, f"未知域名特征({domain})"
