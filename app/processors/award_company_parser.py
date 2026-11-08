# -*- coding: utf-8 -*-
"""award 公告中标人（win_company）确定性解析器（大件一-②）。

设计原则（延续"规则先行、宁可少给不可编造"）：
- 所有锚点必须命中"机构后缀收尾"（公司/中心/大学…），杜绝抓到地址/标题碎片
- 每个抽取结果带 evidence span（字符级 [start, end)），可直接回原文定位
- 按可信度顺序尝试锚点：冒号直给 > 评审列锚点 > 包号列锚点 > 表头压平

归因依据（2026-08-27 库内 201 条 award 实测）：
- 冒号直给/评审列/包号列三类锚点可确定性补 10+ 条；
- 剩余主体为 PDF 短文本与压平表格，留给混合抽取层（LLM 提议+规则裁判）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 机构后缀白名单：命中才认为是机构名（防抓地址/项目名碎片）
_ORG_TAIL = (
    r"(?:公司|中心|大学|学院|医院|集团|研究院|研究所|事务所|合作社|"
    r"银行|厂|店|站|馆|供应站|服务站)"
)
# 贪婪匹配：压平文本里机构名后紧跟地址，懒匹配会停在中间后缀（如"…中心"而非"…中心有限公司"）
_ORG = rf"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9（）()·\.]{{2,58}}{_ORG_TAIL})"

# 地址污染标记：贪婪候选含这些字时，说明吞进了后面的地址
_ADDR_MARKS = ("省", "市", "区", "县", "路", "街", "号", "楼", "室", "层")

# 锚点（按可信度排序）。每个 pattern 的 group(1) 必须是机构名。
_ANCHORS: list[tuple[str, re.Pattern]] = [
    # 1. 冒号直给：中标供应商：XXX / 成交供应商名称：XXX / 中标单位：XXX
    (
        "colon_direct",
        re.compile(
            r"(?:中标|成交|中选)\s*(?:供应商|人|单位)\s*(?:名称)?\s*[:：]\s*" + _ORG
        ),
    ),
    # 2. 供应商名称冒号直给：中标（成交）信息供应商名称：XXX（id=466 主导结构）
    (
        "colon_supplier_name",
        re.compile(r"供应商名称\s*[:：]\s*" + _ORG),
    ),
    # 3. 评审列锚点：评审总得分/评审得分 后紧跟机构名（压平表格行首；
    #    表头后数字为首行序号，bid-winning 公告按名次排列，首行即中标人）
    (
        "review_score_anchor",
        re.compile(r"评审(?:总得分|得分|报价|价格)\s*\d*\.?\d*\s*分?\s*" + _ORG),
    ),
    # 3. 表头压平：供应商地址/供应商联系电话 列名后首个机构名
    (
        "header_flatten",
        re.compile(r"(?:供应商地址|供应商联系电话)\s*" + _ORG),
    ),
]

# 排除词：命中说明抓到的是表头自身或采购方，不是中标人
_BAD_SUBSTRINGS = ("供应商名称", "供应商地址", "采购单位", "招标人")

# 前缀噪声：压平文本里机构名前常粘连“XX包中标人”等标签，需剪掉
_NOISE_PREFIX = re.compile(
    r"^.*?包?\s*(?:中标人|中标供应商|成交供应商|中选供应商|供应商)"
)


def _strip_noise_prefix(value: str) -> str:
    """剪掉机构名前粘连的标签前缀（如“01包中标人XX公司”→“XX公司”）。"""
    m = _NOISE_PREFIX.match(value)
    if m:
        return value[m.end():]
    return value


def _truncate_addr_contamination(value: str) -> str:
    """贪婪候选吞进地址时，截回地址前最后一个机构后缀处。

    例：“成都鲸弘…消毒供应中心有限公司成都市温江区xxx路123号”
    → 首个地址标记“市”前的最后后缀“有限公司”处截断。
    无地址污染则原样返回。
    """
    first_mark = min(
        (value.find(mark) for mark in _ADDR_MARKS if value.find(mark) != -1),
        default=-1,
    )
    if first_mark == -1:
        return value
    # 找地址标记前的最后一个机构后缀结束位置
    head = value[:first_mark]
    cut = -1
    for m in re.finditer(_ORG_TAIL, head):
        cut = m.end()
    if cut <= 0:
        return value  # 地址标记前无后缀，不敢截，宁缺由后续校验拒收
    return value[:cut]


@dataclass(frozen=True)
class WinCompanyResult:
    """中标人抽取结果（带证据 span）。"""

    value: str
    anchor: str  # 命中的锚点名（可观测）
    span_start: int
    span_end: int


def parse_win_company(text: str | None) -> WinCompanyResult | None:
    """从 award 公告正文抽取中标人。None/空文本安全。

    Returns:
        WinCompanyResult（含值、锚点名、字符级 span）或 None（宁缺毋滥）
    """
    if not text:
        return None
    for anchor_name, pat in _ANCHORS:
        m = pat.search(text)
        if not m:
            continue
        value = m.group(1).strip()
        if any(bad in value for bad in _BAD_SUBSTRINGS):
            continue
        value = _strip_noise_prefix(value).strip()
        value = _truncate_addr_contamination(value)
        # 剪噪/截断后必须仍以机构后缀收尾，否则拒收（宁缺毋滥）
        if not value or not re.search(_ORG_TAIL + r"$", value):
            continue
        # span 对齐到原文（剪噪后值在原文的精确位置）
        start = text.find(value, m.start(1))
        if start == -1 or start > m.end(1):
            continue
        return WinCompanyResult(
            value=value, anchor=anchor_name, span_start=start, span_end=start + len(value)
        )
    return None
