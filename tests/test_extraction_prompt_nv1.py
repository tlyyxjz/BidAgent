# -*- coding: utf-8 -*-
"""nv1 守卫：抽取 prompt 三条语义防错位规则存在性断言。

背景：金标 598 空值误报归因（nv1）发现三类语义错位：
1. 更正公告把"招标控制价"引用数值当成 amount（金标口径 168 absent/11 present）
2. 新闻类文档把报道机构当成 purchaser_name
3. 采购代理机构被当成采购人
4. w6 扩源验证：从代理费费率档位反推编造出原文不存在的中标金额
"""
from app.llm.extractor_prompts import EXTRACTION_SYSTEM_PROMPT


class TestNv1PromptRules:
    """nv1 语义防错位规则必须存在于抽取 prompt。"""

    def test_correction_amount_excludes_ceiling_price(self):
        """更正公告 amount 规则排除招标控制价/最高限价。"""
        assert "招标控制价/最高限价" in EXTRACTION_SYSTEM_PROMPT
        assert "控制价是限价而非成交/预算金额" in EXTRACTION_SYSTEM_PROMPT

    def test_non_notice_doc_rule(self):
        """非公告类文档（新闻等）仅抽 publish_date，其余字段 absent。"""
        assert "非公告类文档" in EXTRACTION_SYSTEM_PROMPT
        assert "仅抽取 publish_date" in EXTRACTION_SYSTEM_PROMPT

    def test_agency_is_not_purchaser(self):
        """采购代理机构不得顶替采购人。"""
        assert "采购代理机构不是采购人" in EXTRACTION_SYSTEM_PROMPT

    def test_amount_no_fabrication(self):
        """amount 严禁从费率/代理费反推编造原文不存在的金额。"""
        assert "严禁编造" in EXTRACTION_SYSTEM_PROMPT
        assert "反推/估算出一个原文不存在的金额" in EXTRACTION_SYSTEM_PROMPT
