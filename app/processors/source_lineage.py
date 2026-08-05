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

from app.processors.simhash import compute_simhash, hamming_distance
from app.utils.logger import get_logger

# v4.1 §8.1 转载识别与冲突判定拆分至子模块，此处 re-export 保持公开接口不变
from app.processors.source_lineage_conflict import (  # noqa: F401
    ConflictJudgment,
    judge_field_conflict,
)
from app.processors.source_lineage_repost import (  # noqa: F401
    REPOST_MATCH_THRESHOLD,
    RepostFeatures,
    _title_similarity,
    compute_repost_features,
    find_repost_candidates,
    judge_repost_with_features,
)
# v4.1 第八章来源角色判定拆分至子模块，此处 re-export 保持公开接口不变
from app.processors.source_lineage_role import (  # noqa: F401
    SOURCE_ROLE_AUTHORIZED_ORIGINAL,
    SOURCE_ROLE_COMMERCIAL_REPOST,
    SOURCE_ROLE_INDEX_ONLY,
    SOURCE_ROLE_OFFICIAL_ORIGINAL,
    SOURCE_ROLE_OFFICIAL_REPOST,
    SOURCE_ROLE_UNKNOWN,
    VALID_SOURCE_ROLES,
    _COMMERCIAL_DOMAIN_KEYWORDS,
    _OFFICIAL_DOMAIN_KEYWORDS,
    _extract_domain,
    judge_source_role,
)

logger = get_logger("source_lineage")


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


# ========== 语义化别名（供包级导出）==========

generate_source_lineage = judge_source_lineage
determine_source_role = judge_source_role
