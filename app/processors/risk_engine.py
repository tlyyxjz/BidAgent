"""
鐗堟潈澹版槑锛歅roprietary and Confidential. All rights reserved.

鏈枃浠朵负 BidAgent Open Core 妯″紡涓嬬殑涓撴湁浠ｇ爜锛屼笉鍦?Apache License 2.0 鎺堟潈鑼冨洿鍐呫€?浠呬緵璇勪及銆佸鏈瓟杈╀笌鍟嗕笟鎺堟潈瀹㈡埛浣跨敤銆傛湭缁忎功闈㈣鍙紝涓嶅緱鐢ㄤ簬鍟嗕笟鐢熶骇鐜銆?
濡傞渶鍟嗕笟鎺堟潈锛岃鑱旂郴锛?3566878907@163.com

Copyright 2026 寰愭禋閽? 鐜嬬ク鏄?(鏍囧皬鏅哄洟闃?. All rights reserved.
"""
"""废标风险预警引擎。

提供排他性条款、付款风险、交货期、资质门槛等多维度风险扫描，
输出 RiskReport 含 risk_score / risk_items / qualification_gaps。

修复完整版 6 个 bug + 审查 4 个问题：
1. _analyze 参数顺序统一为 (project_name, content, qualification)
2. 星号规则单独处理，不混入通用分支
3. 多词组合规则增加 mode 字段（any/all/star）
4. seen 去重改为规则索引，避免跨规则误跳过
5. 主导出 analyze_risk（与 analyze_boq 命名一致），analyze_risk_engine 作别名
6. 同步工作卸载到 run_in_executor
7. M-3: engine 字段改为 rule_based_v1（纯规则引擎，不蹭 LLM 名字）
8. M-1: 增加 total_risk_items + total_risk_score 字段，解决截断与分数不一致
9. M-2: 否定语境检测（不/无需/没有/不要求/不接受 + 关键词 → 不命中）
10. m-1: 星号规则只匹配 ★，不匹配 *（避免 HTML/Markdown 误报）
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app.utils.logger import get_logger

logger = get_logger("risk_engine")


@dataclass
class RiskReport:
    """废标风险分析报告。"""

    tender_id: int | None = None
    project_name: str = ""
    risk_score: float = 0.0
    summary: str = ""
    risk_items: list[dict[str, Any]] = field(default_factory=list)
    qualification_gaps: list[str] = field(default_factory=list)
    created_at: str = ""
    # M-1 修复：完整统计（不截断）
    total_risk_items: int = 0
    total_risk_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "version": "v2",
            "tender_id": self.tender_id,
            "project_name": self.project_name,
            # 展示用：前 10 条（与 risk_items 对齐）
            "risk_score": round(self.risk_score, 1),
            "risk_level": self._level(),
            # M-1 修复：完整统计字段（不截断）
            "total_risk_items": self.total_risk_items,
            "total_risk_score": round(self.total_risk_score, 1),
            "summary": self.summary,
            "risk_items": self.risk_items[:10],
            "qualification_gaps": self.qualification_gaps,
            "created_at": (
                self.created_at or time.strftime("%Y-%m-%d %H:%M:%S")
            ),
            # M-3 修复：诚实标注纯规则引擎
            "engine": "rule_based_v1",
        }

    def _level(self) -> str:
        """风险等级（按展示分数计算）。"""
        if self.risk_score >= 60:
            return "高风险"
        if self.risk_score >= 30:
            return "中风险"
        return "低风险"


# 规则定义：(keywords, mode, type, desc, suggestion, points, law)
# mode: "any" 任一关键词命中 / "all" 全部命中 / "star" 星号检测
_RULES: list[tuple[list[str], str, str, str, str, float, str]] = [
    # --- 排他性规则 ---
    (["必须具备"], "any", "exclusive", "排他性资质要求，限制竞争",
     "核查该条款是否符合《招标投标法》公平竞争要求", 20,
     "《招标投标法实施条例》第三十二条"),
    (["必须拥有"], "any", "exclusive", "排他性资质要求",
     "核查法律法规依据", 20, ""),
    (["独有"], "any", "exclusive", "独家授权条款，可能针对特定供应商",
     "如无法律依据可提出质疑", 25, "《招标投标法》第二十条"),
    (["唯一授权"], "any", "exclusive", "唯一授权要求",
     "需确认是否属于合法排他", 25, ""),
    (["原厂"], "any", "exclusive", "原厂授权要求，可能限制竞争",
     "要求提供至少三家品牌竞争", 20,
     "《招标投标法实施条例》第三十二条"),
    (["指定品牌"], "any", "exclusive", "指定品牌要求，涉嫌量身定做",
     "建议要求公开品牌选择标准", 20, ""),
    (["注册资金不低于", "注册资本不低于"], "any", "exclusive",
     "注册资本门槛限制中小企业参与",
     "除非法律有明确规定，否则涉嫌歧视", 15,
     "《政府采购促进中小企业发展管理办法》"),

    # --- 付款/保证金风险 ---
    (["付款周期", "付款期限"], "any", "payment",
     "需核实付款周期是否超过60天",
     "长期付款影响现金流，请评估", 15, ""),
    (["履约保证金"], "any", "payment",
     "需确认保证金比例是否超过10%",
     "超过10%可依法要求降低", 12,
     "《招标投标法实施条例》第五十八条"),
    (["现金"], "any", "payment",
     "仅接受现金保证金，增加投标人负担",
     "建议要求接受银行保函", 10, ""),

    # --- 交货期风险 ---
    (["交货期", "交付期", "供货期"], "any", "deadline",
     "交货期过短可能导致履约困难",
     "确认是否有足够生产/备货时间", 12, ""),

    # --- 资质门槛 ---
    (["资质", "许可证"], "any", "qualification",
     "需确认企业是否具备相关资质",
     "提前准备资质证明材料", 8, ""),
    (["ISO"], "any", "qualification",
     "ISO体系认证要求，需确认持有情况",
     "提前准备认证证书", 8, ""),
    (["CMMI"], "any", "qualification",
     "CMMI认证要求",
     "确认CMMI等级要求", 8, ""),
    (["安全生产"], "any", "qualification",
     "安全生产许可要求",
     "确认安全生产许可证有效期内", 8, "《安全生产法》"),

    # --- 联合规则（DeepSeek V4 Pro 贡献） ---
    (["本地", "本市", "本省"], "any", "exclusive",
     "本地化业绩要求+资格条件叠加，废标高发",
     "组合条款可能排斥外地企业，建议评估", 18,
     "《招标投标法》第六条"),
    # m-1 修复：星号规则只匹配 ★（中文星号），不匹配 *
    # 原因：* 在 HTML 标签、Markdown 格式、列表标记中极其常见，误报率高
    (["★"], "star", "exclusive",
     "全部技术参数标注星号且无偏差条款",
     "可能针对特定品牌参数设计", 20,
     "《招标投标法实施条例》第三十二条"),
    (["联合体", "分包"], "all", "other",
     "联合体投标与分包条款矛盾",
     "文件允许联合体但禁止分包构成逻辑冲突", 15, ""),
    (["否决"], "any", "other",
     "否决条款密集但未提及书面澄清渠道",
     "多项否决权缺乏救济渠道", 12,
     "《招标投标法》第四十四条"),
]


# M-2 修复：否定语境检测
# 当关键词前出现否定词时，规则不应命中（如"不要求原厂授权"）
_NEGATION_WORDS = (
    "不要求", "不需要", "无需", "没有", "不限制", "不限", "不接受",
    "不设", "不强制", "非强制",
)


def _is_negated(keyword: str, content: str, window: int = 10) -> bool:
    """检测关键词前是否紧邻否定词。

    Args:
        keyword: 命中的关键词
        content: 完整文本
        window: 向前查找的字符窗口

    Returns:
        True 表示关键词处于否定语境中，规则不应命中
    """
    if not keyword or not content:
        return False

    idx = content.find(keyword)
    while idx != -1:
        # 取关键词前 window 个字符
        prefix = content[max(0, idx - window):idx]
        for neg in _NEGATION_WORDS:
            if neg in prefix:
                return True
        idx = content.find(keyword, idx + 1)

    return False


def _match_rule(
    keywords: list[str],
    mode: str,
    content: str,
) -> bool:
    """根据 mode 判断规则是否命中。"""
    if mode == "all":
        return all(kw in content for kw in keywords)
    if mode == "star":
        return any(kw in content for kw in keywords)
    # 默认 any
    return any(kw in content for kw in keywords)


def _analyze(
    project_name: str,
    content: str,
    qualification: str | None,
) -> RiskReport:
    """同步执行废标风险分析。

    Args:
        project_name: 项目名称
        content: 招标公告正文
        qualification: 资质要求文本（可空）

    Returns:
        RiskReport 对象
    """
    report = RiskReport(project_name=project_name)
    report.created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    full_text = f"{project_name} {content} {qualification or ''}"
    items: list[dict[str, Any]] = []
    total_score = 0.0  # M-1: 全部分数（不截断）
    seen_rules: set[int] = set()

    for rule_idx, (keywords, mode, rtype, desc, suggestion, points, law) in enumerate(_RULES):
        if rule_idx in seen_rules:
            continue
        if not _match_rule(keywords, mode, full_text):
            continue

        # M-2 修复：否定语境检测
        # 检查命中的关键词是否处于否定语境
        matched_kws = [kw for kw in keywords if kw in full_text]
        negated_kws = [kw for kw in matched_kws if _is_negated(kw, full_text)]
        # AND 规则：任一关键词被否定即破坏组合语义，规则不命中
        # ANY/STAR 规则：过滤掉被否定的关键词，仍有非否定命中则继续
        if mode == "all":
            if negated_kws:
                continue
        else:
            # ANY/STAR：过滤掉被否定的关键词，如果全被否定则不命中
            non_negated = [kw for kw in matched_kws if kw not in negated_kws]
            if not non_negated:
                continue
            matched_kws = non_negated

        seen_rules.add(rule_idx)

        items.append({
            "clause": keywords[0],
            "risk_level": "high" if points >= 15 else "medium",
            "risk_type": rtype,
            "description": desc,
            "suggestion": suggestion,
            "law_ref": law,
            "matched_keywords": matched_kws,
        })
        total_score += points

    # 资质缺口分析
    gaps: list[str] = []
    if qualification:
        if "资质" not in full_text and "许可证" not in full_text:
            gaps.append("未明确列出所需资质清单，需向招标方确认")
        if "ISO" in full_text:
            gaps.append("ISO认证（需确认覆盖范围）")
        if "CMMI" in full_text or "系统集成" in full_text:
            gaps.append("CMMI或系统集成资质（需确认等级要求）")

    # M-1 修复：分数与展示一致
    # risk_score 和 total_risk_score 都用完整分数（封顶 100）
    # risk_items 在 to_dict 中截断到前 10 条展示
    # total_risk_items / total_risk_score 提供完整统计
    capped_score = min(total_score, 100.0)
    report.risk_score = capped_score
    report.total_risk_score = capped_score
    report.total_risk_items = len(items)
    report.risk_items = items  # 完整列表，to_dict 时截断到 10
    report.qualification_gaps = gaps

    # 智能摘要（用全部 items 统计）
    high = sum(1 for i in items if i["risk_level"] == "high")
    medium = sum(1 for i in items if i["risk_level"] == "medium")
    excl = sum(1 for i in items if i["risk_type"] == "exclusive")
    parts: list[str] = []
    if high > 0:
        parts.append(f"{high}项高风险")
    if excl > 0:
        parts.append(f"{excl}项可能涉及排他")
    if medium > 0:
        parts.append(f"{medium}项中风险")
    if gaps:
        parts.append(f"{len(gaps)}个资质缺口")
    report.summary = "，".join(parts) + "。" if parts else "未检测到明显风险。"

    return report


async def analyze_risk(
    project_name: str,
    content: str,
    qualification: str | None = None,
    tender_id: int | None = None,
) -> dict[str, Any]:
    """异步分析废标风险，将同步规则匹配卸载到执行器。"""
    loop = asyncio.get_running_loop()
    task = partial(_analyze, project_name or "", content or "", qualification)
    report = await loop.run_in_executor(None, task)
    report.tender_id = tender_id
    return report.to_dict()


# 兼容别名（与完整版 analyze_risk_engine 命名兼容）
analyze_risk_engine = analyze_risk
