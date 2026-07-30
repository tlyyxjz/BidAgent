"""W3-01 来源谱系判定引擎。

对应总规划 v4.1 第六章 6.3「来源谱系判定」+ 第八章「来源角色」。

核心职责：
1. 判定页面来源角色（official_original / official_repost / commercial_repost / unknown）
2. 同源转载候选识别（SimHash 汉明距离 ≤ 3）
3. 来源谱系组生成（同源转载归为同一 source_group）
4. 事实断言键生成（用于版本差异和事实冲突区分）

W3 周验收要求：
- 同一项目不同公告不会被误判为冲突
- 同一公告转载不会被误判为独立验证

工程约束：
- 纯函数 + 数据类，不绑定数据库类型
- SimHash 阈值 ≤ 3（复用 app.processors.simhash）
- 幂等：相同输入产生相同输出
- 来源角色判定基于 URL 域名 + 内容特征（不依赖 LLM）
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from app.processors.simhash import compute_simhash, hamming_distance
from app.utils.logger import get_logger

logger = get_logger("source_lineage")


# ========== 来源角色枚举（v4.1 第八章）==========

SOURCE_ROLE_OFFICIAL_ORIGINAL = "official_original"
SOURCE_ROLE_OFFICIAL_REPOST = "official_repost"
SOURCE_ROLE_COMMERCIAL_REPOST = "commercial_repost"
SOURCE_ROLE_UNKNOWN = "unknown"

VALID_SOURCE_ROLES = (
    SOURCE_ROLE_OFFICIAL_ORIGINAL,
    SOURCE_ROLE_OFFICIAL_REPOST,
    SOURCE_ROLE_COMMERCIAL_REPOST,
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


# ========== 数据类 ==========

@dataclass
class SourceLineageResult:
    """来源谱系判定结果。"""
    # 来源角色
    source_role: str
    # SimHash 指纹（64 位）
    simhash: int
    # 来源谱系组 ID（同源转载归为同一组，SHA256(source_url + simhash) 前 16 字符）
    source_group: str
    # 事实断言键（field_name + normalized_value 的 SHA256，用于跨公告冲突检测）
    fact_assertion_key: Optional[str] = None
    # 判定理由
    reason: str = ""
    # 同源转载候选（SimHash 候选列表，由调用方填充）
    repost_candidates: list = field(default_factory=list)


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
) -> tuple[str, str]:
    """判定页面来源角色。

    判定规则（v4.1 第八章，优先级从高到低）：
    1. is_original_publication=True → official_original（调用方明确标记为首发）
    2. 域名包含官方关键词 → official_original 或 official_repost
       - 若 content_text 中含"转载""来源："等标记 → official_repost
       - 否则 → official_original
    3. 域名包含商业关键词 → commercial_repost
    4. 其余 → unknown

    Args:
        source_url: 来源页面 URL
        content_text: 页面内容（用于检测转载标记）
        is_original_publication: 调用方明确标记为首发

    Returns:
        (source_role, reason)
    """
    if is_original_publication:
        return SOURCE_ROLE_OFFICIAL_ORIGINAL, "调用方标记为首发"

    domain = _extract_domain(source_url)
    if not domain:
        return SOURCE_ROLE_UNKNOWN, "无法解析域名"

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
            return SOURCE_ROLE_COMMERCIAL_REPOST, f"商业域名({domain})"

    # 未知域名，但含转载标记
    if has_repost_marker:
        return SOURCE_ROLE_COMMERCIAL_REPOST, f"非官方域名({domain})且含转载标记"

    return SOURCE_ROLE_UNKNOWN, f"未知域名特征({domain})"


# ========== 来源谱系组生成 ==========

def compute_source_group(source_url: str, simhash: int) -> str:
    """计算来源谱系组 ID。

    同一原文被多次转载时，SimHash 相同或汉明距离 ≤ 3，归为同一 source_group。

    Args:
        source_url: 来源 URL
        simhash: SimHash 指纹

    Returns:
        source_group ID（SHA256(source_url + simhash) 前 16 字符）
    """
    raw = f"{source_url}|{simhash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ========== 事实断言键生成 ==========

def compute_fact_assertion_key(
    field_name: str,
    normalized_value: str,
    project_identifier: str = "",
) -> str:
    """计算事实断言键（用于跨公告冲突检测）。

    设计原则（W3 周验收要求）：
    - 同一项目不同公告（招标/中标/更正）的同字段同值 → 同一断言键（非冲突）
    - 同一项目不同公告的同字段不同值 → 不同断言键（版本差异，非冲突）
    - 不同项目的同字段同值 → 不同断言键（project_identifier 隔离）
    - 同一公告转载 → 同一断言键（不视为独立验证）

    Args:
        field_name: 字段名
        normalized_value: 规范化后的字段值
        project_identifier: 项目编号（用于隔离不同项目）

    Returns:
        fact_assertion_key（SHA256 前 16 字符）
    """
    raw = f"{project_identifier}|{field_name}|{normalized_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ========== 同源转载候选识别 ==========

def find_repost_candidates(
    target_simhash: int,
    candidates: list[tuple[str, int, str]],
    threshold: int = 3,
) -> list[tuple[str, int, str]]:
    """在同源转载候选集合中查找匹配项。

    Args:
        target_simhash: 待查 SimHash
        candidates: 候选列表，每个元素是 (source_id, simhash, source_url)
        threshold: 汉明距离阈值（默认 3）

    Returns:
        匹配的候选列表（按汉明距离升序）
    """
    if target_simhash == 0:
        return []
    matched = []
    for source_id, cand_hash, source_url in candidates:
        if cand_hash == 0:
            continue
        dist = hamming_distance(target_simhash, cand_hash)
        if dist <= threshold:
            matched.append((source_id, cand_hash, source_url))
    matched.sort(key=lambda x: hamming_distance(target_simhash, x[1]))
    return matched


# ========== 主判定函数 ==========

def judge_source_lineage(
    source_url: str,
    content_text: str,
    *,
    is_original_publication: bool = False,
    field_name: str = "",
    normalized_value: str = "",
    project_identifier: str = "",
    repost_candidates: list[tuple[str, int, str]] | None = None,
) -> SourceLineageResult:
    """来源谱系判定主函数。

    流程：
    1. 判定来源角色
    2. 计算 SimHash
    3. 生成来源谱系组
    4. 查找同源转载候选
    5. 生成事实断言键（如提供字段信息）

    Args:
        source_url: 来源页面 URL
        content_text: 页面内容
        is_original_publication: 是否首发
        field_name: 字段名（可选，用于事实断言键）
        normalized_value: 规范化字段值（可选，用于事实断言键）
        project_identifier: 项目编号（可选，用于事实断言键隔离）
        repost_candidates: 同源转载候选列表 [(source_id, simhash, source_url)]

    Returns:
        SourceLineageResult
    """
    # 1. 来源角色
    source_role, reason = judge_source_role(
        source_url, content_text=content_text, is_original_publication=is_original_publication
    )

    # 2. SimHash
    simhash = compute_simhash(content_text)

    # 3. 来源谱系组
    source_group = compute_source_group(source_url, simhash)

    # 4. 同源转载候选
    matched_candidates = []
    if repost_candidates:
        matched_candidates = find_repost_candidates(simhash, repost_candidates)

    # 5. 事实断言键
    fact_key = None
    if field_name and normalized_value:
        fact_key = compute_fact_assertion_key(field_name, normalized_value, project_identifier)

    return SourceLineageResult(
        source_role=source_role,
        simhash=simhash,
        source_group=source_group,
        fact_assertion_key=fact_key,
        reason=reason,
        repost_candidates=matched_candidates,
    )


# ========== 版本差异 vs 事实冲突区分 ==========

@dataclass
class ConflictJudgment:
    """冲突判定结果。"""
    is_conflict: bool
    is_version_diff: bool
    reason: str


def judge_field_conflict(
    fact_key_a: str,
    fact_key_b: str,
    field_name: str,
    value_a: str,
    value_b: str,
    project_identifier: str,
    notice_type_a: str,
    notice_type_b: str,
) -> ConflictJudgment:
    """判断两个字段值是版本差异还是事实冲突。

    W3 周验收要求：
    - 同一项目不同公告（招标/中标/更正）不会被误判为冲突
    - 同一公告转载不会被误判为独立验证

    判定规则：
    1. fact_key 相同 → 非冲突（同值）
    2. fact_key 不同但 project_identifier 相同 + notice_type 不同 → 版本差异
       （如招标公告预算 vs 中标公告合同金额，是正常的版本演进）
    3. fact_key 不同且 project_identifier 相同 + notice_type 相同 → 事实冲突
       （同一公告类型同一项目同一字段不同值，可能是数据错误）
    4. project_identifier 不同 → 不比较（不同项目）

    Args:
        fact_key_a: 字段 A 的事实断言键
        fact_key_b: 字段 B 的事实断言键
        field_name: 字段名
        value_a: 字段 A 的值
        value_b: 字段 B 的值
        project_identifier: 项目编号
        notice_type_a: 公告 A 类型 (tender/award/correction)
        notice_type_b: 公告 B 类型

    Returns:
        ConflictJudgment
    """
    # fact_key 相同 → 非冲突
    if fact_key_a == fact_key_b:
        return ConflictJudgment(
            is_conflict=False, is_version_diff=False,
            reason="事实断言键相同，同值非冲突"
        )

    # 不同项目不比较
    if not project_identifier:
        return ConflictJudgment(
            is_conflict=False, is_version_diff=False,
            reason="缺少项目编号，无法判断"
        )

    # 同项目同公告类型同字段不同值 → 事实冲突
    if notice_type_a == notice_type_b:
        return ConflictJudgment(
            is_conflict=True, is_version_diff=False,
            reason=f"同项目({project_identifier})同公告类型({notice_type_a})"
                   f"同字段({field_name})不同值: '{value_a}' vs '{value_b}'"
        )

    # 同项目不同公告类型同字段不同值 → 版本差异
    return ConflictJudgment(
        is_conflict=False, is_version_diff=True,
        reason=f"同项目({project_identifier})不同公告类型"
               f"({notice_type_a}→{notice_type_b})字段({field_name})"
               f"值变化: '{value_a}' → '{value_b}'"
    )
