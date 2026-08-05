"""W2-03 确定性证据搜索与验证引擎。

对应总规划 v4.1 第六章 6.1「程序逐段搜索候选证据」+ 第六章 6.2 抽取支持度。

实现 5 级降级匹配策略：
- L1 精确匹配：候选证据文本在原文中直接出现
- L2 去空白匹配：忽略空白差异后匹配
- L3 去标点匹配：忽略标点符号差异后匹配（Day 2 实现）
- L4 核心子串匹配：取候选证据的核心子串进行匹配（Day 2 实现）
- L5 失败：标记为 unsupported

本文件 Day 1 实现 L1 + L2。

工程约束：
- 纯确定性算法，不调用 LLM
- 性能目标：小于 20KB 文本 P95 不超过 200ms
- 找不到证据时必须标记为 unsupported，不得伪造
- 匹配结果生成 EvidenceLocation，包含 start/end/text/match_type/confidence
- 支持 search_from 参数控制匹配起始位置
- 支持批量定位接口（解决同名文本多次出现）
"""
from app.processors.evidence_locator._models import (
    EVIDENCE_RULE_VERSION,
    EvidenceLocation,
    LocateResult,
    MatchType,
    SupportLevel,
)
from app.processors.evidence_locator._locator import EvidenceLocator
from app.processors.evidence_locator._verify import verify_evidence

__all__ = [
    "EVIDENCE_RULE_VERSION",
    "EvidenceLocation",
    "EvidenceLocator",
    "LocateResult",
    "MatchType",
    "SupportLevel",
    "verify_evidence",
]
