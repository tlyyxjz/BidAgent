# -*- coding: utf-8 -*-
"""证据偏移解析：把「证据文本 + 库内偏移」解析成「原文空间里可信的字符区间」。

背景（2026-10-08 前后端对齐审查，结论均以库内数据实查为依据）：

1. **坐标基准是清洗后正文，不是 `source_raw_text`。**
   `evidence.raw_start/raw_end` 的基准是 `tenders.core_content`（前端称 `clean_raw_text`）。
   字段名里的 "raw" 是历史命名，与 `tenders.source_raw_text` **不是同一个字符串**——
   后者比前者多一段 `## 标题 / # URL:` 头部，长度因文档而异。
   一旦把这两者混用，高亮就会整体漂移（这正是「定位有点飘」的命名陷阱）。

2. **偏移校验与重定位以前散落在浏览器里。**
   前端 `notice_detail.html` 的旧逻辑是：切片对不上就用全文 `indexOf` 找。
   `indexOf` 返回**首次出现**位置，而短证据（日期 / 金额 / 地区）在原文里往往出现多次
   ⇒ 高亮会跳到文档里另一处相同文本上，看起来就是"乱飘"。

本模块把「切片校验 + 安全重定位」收到服务端一处，接口只返回结论，前端不再猜：

- ``slice_ok``  ：库内偏移切片与证据文本逐字一致（偏移本身可信）
- ``relocated`` ：库内偏移失效，但证据文本在本篇原文中可**唯一**定位，已改用新偏移
- ``locatable`` ：证据文本能否在本篇原文中定位。``False`` 表示孤儿证据
                  （原文已被重抓覆盖、该文本已不存在），前端应跳过高亮

历史遗留（不可恢复，仅记录）：`tender_id` 1–5 的正文于 2026-08-05 被重抓覆盖成别的
公告，旧证据仍挂在同一 id 上，证据文本在 `core_content`、`source_raw_text` 与
`data/snapshots/` 快照中都查不到 ⇒ 只能判为 ``locatable=False``。
"""
from __future__ import annotations

from typing import Optional, Tuple

# 就近重定位的搜索窗口（字符）。优先在期望位置附近找，避免命中文档别处的相同文本。
NEAR_WINDOW = 400


def _unique_index(hay: str, needle: str) -> int:
    """返回 ``needle`` 在 ``hay`` 中**唯一**命中的下标；无命中或多处命中返回 -1。

    多处命中时返回 -1 是刻意的：短证据（日期 / 金额 / 地区）在公告里往往重复出现，
    此时任何"取第一个"的策略都会把高亮挪到错误位置——宁可判为不可定位。
    """
    first = hay.find(needle)
    if first < 0:
        return -1
    return -1 if hay.find(needle, first + len(needle)) >= 0 else first


def resolve_evidence_span(
    raw_text: str,
    start: Optional[int],
    end: Optional[int],
    text: Optional[str],
) -> Tuple[Optional[int], Optional[int], bool, bool, bool]:
    """解析单条证据在 ``raw_text`` 中的可信区间。

    Args:
        raw_text: 该公告的清洗后正文，即 `tenders.core_content`（前端 `clean_raw_text`）。
        start/end: 库内偏移，基准同上；``end`` 为**排他**上界。
        text: 证据原文。

    Returns:
        ``(start, end, slice_ok, relocated, locatable)``。
        ``locatable=False`` 时 ``start/end`` 原样返回，前端应跳过高亮。

    决策顺序（每一步都要求"不产生歧义"才接受）：
        1. 库内偏移切片与文本逐字一致 ⇒ 直接采信（哪怕该文本在文中多次出现）；
        2. 期望位置附近的窗口内**唯一**命中 ⇒ 重定位（正文被小幅改写的情形）；
        3. 全文**唯一**命中 ⇒ 重定位；
        4. 否则判为不可定位。
    """
    raw_text = raw_text or ""
    text = text or ""

    if not text:
        return start, end, False, False, False

    # 1) 库内偏移直接可用（最高优先级，不动它）
    if (
        start is not None
        and end is not None
        and 0 <= start < end <= len(raw_text)
        and raw_text[start:end] == text
    ):
        return start, end, True, False, True

    # 2) 就近窗口内唯一命中
    lo = max(0, (start or 0) - NEAR_WINDOW)
    hi = min(len(raw_text), max(end or 0, (start or 0)) + NEAR_WINDOW)
    if hi > lo:
        at = _unique_index(raw_text[lo:hi], text)
        if at >= 0:
            return lo + at, lo + at + len(text), False, True, True

    # 3) 全文唯一命中才接受
    at = _unique_index(raw_text, text)
    if at >= 0:
        return at, at + len(text), False, True, True

    # 4) 孤儿证据或存在歧义：不猜
    return start, end, False, False, False


def summarize(spans) -> dict:
    """对一批解析结果做统计（供接口返回与门禁脚本复用）。

    Args:
        spans: ``resolve_evidence_span`` 的返回元组序列。
    """
    total = ok = relocated = orphan = 0
    for (_s, _e, _slice_ok, _reloc, locatable) in spans:
        total += 1
        if not locatable:
            orphan += 1
        elif _reloc:
            relocated += 1
        else:
            ok += 1
    return {
        "total": total,
        "ok": ok,
        "relocated": relocated,
        "orphan": orphan,
    }
