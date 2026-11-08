"""跨源交叉验证服务（可查验增强·多源佐证链）。

背景与口径：
- 库内已接入 ccgp（全国库）+ 九个省市分站。同一采购项目常在
  国家级平台与省级平台同时公示，编号体系同源（如江苏 JSZC-*、
  山东 SDGP*、云南 YNZC* 在全国库与分站同时出现）。
- 本模块回答两个问题：
  1. 某条公告能否在**其他平台**找到同一项目 → 佐证（corroborated）
  2. 找不到 → 孤证标注（single_source），明示"仅单源、未交叉验证"

设计原则（诚实优先，宁可少给不可编造）：
- 匹配只发生在**不同 source_platform** 之间（同平台重复不算佐证）
- 两级证据，级别字段如实返回，不合并、不美化：
  L1 bid_number_strict：归一化项目编号相等（确定性证据）
  L2 title_strong：标题字符 Jaccard 相似度 ≥ 0.80（强标题证据）
- 无编号且标题不足 → 不产生匹配（不猜）
- 纯函数，不绑定数据库类型，全量可单测

工程约束：
- 幂等：相同输入产生相同输出
- 不依赖 LLM / 网络
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.processors.simhash import hamming_distance
from app.processors.source_lineage_repost import _title_similarity

# 匹配阈值
TITLE_STRONG_THRESHOLD = 0.80  # L2 标题相似度门槛
SIMHASH_NEAR_THRESHOLD = 8     # 跨源 SimHash 近邻辅助证据门槛（比同源宽松，
#                              # 因不同平台排版/前缀差异大）

# 匹配级别（顺序即证据强度）
LEVEL_BID_NUMBER = "bid_number_strict"
LEVEL_TITLE_STRONG = "title_strong"

# 佐证状态
STATUS_CORROBORATED = "corroborated"
STATUS_SINGLE_SOURCE = "single_source"


@dataclass
class NoticeRef:
    """公告轻量引用（纯数据，与 ORM 解耦）。"""
    tender_id: int
    source_platform: str
    bid_number: str | None = None
    project_name: str | None = None
    simhash: int | None = None


@dataclass
class CrossMatch:
    """单条跨源匹配结果。"""
    tender_id: int
    source_platform: str
    level: str                 # LEVEL_BID_NUMBER / LEVEL_TITLE_STRONG
    title_similarity: float    # 0.0-1.0
    bid_number_matched: bool
    simhash_near: bool         # 辅助证据（不参与级别判定）
    reason: str


@dataclass
class CorroborationResult:
    """单条公告的佐证判定结果。"""
    status: str                # STATUS_CORROBORATED / STATUS_SINGLE_SOURCE
    matches: list[CrossMatch] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "match_count": len(self.matches),
            "matches": [
                {
                    "tender_id": m.tender_id,
                    "source_platform": m.source_platform,
                    "level": m.level,
                    "title_similarity": round(m.title_similarity, 3),
                    "bid_number_matched": m.bid_number_matched,
                    "simhash_near": m.simhash_near,
                    "reason": m.reason,
                }
                for m in self.matches
            ],
        }


# ========== 归一化 ==========

def normalize_bid_number(bid_number: str | None) -> str | None:
    """归一化项目编号：去空白、大写、去空格。

    空串/None → None（宁可缺，不用空值匹配）。
    """
    if not bid_number:
        return None
    cleaned = "".join(bid_number.split()).upper()
    return cleaned or None


# ========== 匹配 ==========

def find_cross_matches(
    target: NoticeRef,
    candidates: list[NoticeRef],
    *,
    title_threshold: float = TITLE_STRONG_THRESHOLD,
    simhash_threshold: int = SIMHASH_NEAR_THRESHOLD,
) -> list[CrossMatch]:
    """在候选集中找跨源匹配（只匹配不同平台的候选）。

    规则（按证据强度从高到低）：
    - L1：归一化 bid_number 相等 → bid_number_strict
    - L2：标题相似度 ≥ title_threshold → title_strong
    - SimHash 近邻只作辅助证据标注，不单独升级/降级

    Args:
        target: 待查公告
        candidates: 候选公告列表（可含同平台，内部过滤）
        title_threshold: L2 标题门槛
        simhash_threshold: SimHash 辅助证据门槛

    Returns:
        匹配列表（按证据强度降序、相似度降序）
    """
    tgt_bn = normalize_bid_number(target.bid_number)
    tgt_title = (target.project_name or "").strip()
    matches: list[CrossMatch] = []

    for cand in candidates:
        if cand.source_platform == target.source_platform:
            continue  # 同平台重复不算佐证

        cand_bn = normalize_bid_number(cand.bid_number)
        cand_title = (cand.project_name or "").strip()

        bn_hit = tgt_bn is not None and cand_bn == tgt_bn
        title_sim = _title_similarity(tgt_title, cand_title) \
            if tgt_title and cand_title else 0.0
        sh_near = False
        if target.simhash and cand.simhash:
            sh_near = hamming_distance(target.simhash,
                                       cand.simhash) <= simhash_threshold

        if bn_hit:
            level = LEVEL_BID_NUMBER
            reason = f"项目编号一致: {tgt_bn}"
        elif title_sim >= title_threshold:
            level = LEVEL_TITLE_STRONG
            reason = f"标题相似度 {title_sim:.2f} ≥ {title_threshold:.2f}"
        else:
            continue

        if sh_near:
            reason += "（SimHash 近邻佐证）"

        matches.append(CrossMatch(
            tender_id=cand.tender_id,
            source_platform=cand.source_platform,
            level=level,
            title_similarity=title_sim,
            bid_number_matched=bn_hit,
            simhash_near=sh_near,
            reason=reason,
        ))

    level_rank = {LEVEL_BID_NUMBER: 0, LEVEL_TITLE_STRONG: 1}
    matches.sort(key=lambda m: (level_rank.get(m.level, 9),
                                -m.title_similarity, m.tender_id))
    return matches


def corroborate_notice(
    target: NoticeRef,
    candidates: list[NoticeRef],
    *,
    title_threshold: float = TITLE_STRONG_THRESHOLD,
    simhash_threshold: int = SIMHASH_NEAR_THRESHOLD,
) -> CorroborationResult:
    """对单条公告做佐证判定：有跨源匹配→corroborated，否则孤证标注。"""
    matches = find_cross_matches(
        target, candidates,
        title_threshold=title_threshold,
        simhash_threshold=simhash_threshold,
    )
    status = STATUS_CORROBORATED if matches else STATUS_SINGLE_SOURCE
    return CorroborationResult(status=status, matches=matches)


# ========== 批量报告 ==========

def build_corroboration_report(notices: list[NoticeRef]) -> dict:
    """全库跨源佐证报告（纯函数，O(n²) 但库规模 <1e4 可接受）。

    Returns:
        {
            "total": 总条数,
            "corroborated": 有跨源佐证的条数,
            "single_source": 孤证条数,
            "by_platform": {platform: {"total": n, "corroborated": m}},
            "matches": [ {target_tender_id, target_platform, match:{...}} ],
        }
    """
    corroborated_ids: set[int] = set()
    match_records: list[dict] = []
    for tgt in notices:
        res = corroborate_notice(tgt, notices)
        if res.status == STATUS_CORROBORATED:
            corroborated_ids.add(tgt.tender_id)
            for m in res.matches:
                match_records.append({
                    "target_tender_id": tgt.tender_id,
                    "target_platform": tgt.source_platform,
                    "match_tender_id": m.tender_id,
                    "match_platform": m.source_platform,
                    "level": m.level,
                    "title_similarity": round(m.title_similarity, 3),
                    "reason": m.reason,
                })

    by_platform: dict[str, dict] = {}
    for n in notices:
        slot = by_platform.setdefault(
            n.source_platform, {"total": 0, "corroborated": 0})
        slot["total"] += 1
        if n.tender_id in corroborated_ids:
            slot["corroborated"] += 1

    return {
        "total": len(notices),
        "corroborated": len(corroborated_ids),
        "single_source": len(notices) - len(corroborated_ids),
        "by_platform": by_platform,
        "matches": match_records,
    }
