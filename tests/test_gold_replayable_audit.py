# -*- coding: utf-8 -*-
"""nv2 守卫：金标可重放审计零问题。

全部 present 字段的金标值必须可在原文定位（证据可重放）。
新增/修改金标标注后此测试自动把关。
"""
from scripts.audit_gold_replayable import audit


class TestGoldReplayableAudit:
    """可重放审计守卫。"""

    def test_all_present_values_locatable(self):
        docs, present, problems = audit()
        assert docs >= 598, f"金标文档数异常: {docs}"
        assert present >= 2200, f"present 字段数异常: {present}"
        assert problems == [], \
            f"存在 {len(problems)} 个不可定位金标值: {problems[:5]}"
