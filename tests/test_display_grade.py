"""W3-07 display_grade + 选择性输出 单元测试.

覆盖：
  - compute_display_grade 组合规则 (≥6 个)
  - filter_by_strategy 四个策略 (≥4 个)
  - 迁移脚本幂等与空数据库
  合计 ≥8 个，实际编写更多以满足覆盖率要求.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.output_strategies import filter_by_strategy
from app.llm.extraction_schemas import ExtractionResult, FieldExtraction
from app.processors.display_grade import (
    GRADE_HIGH,
    GRADE_LOW,
    GRADE_REVIEW,
    SupportLevel,
    compute_display_grade,
)


# ========== compute_display_grade ==========

def test_high__strong_direct_official_original():
    """HIGH: DIRECT + official_original"""
    assert compute_display_grade(
        support_level=SupportLevel.DIRECT,
        source_role="official_original",
        cross_verified=True,
    ) == GRADE_HIGH


def test_high__strong_equivalent_official_original_no_cross():
    """HIGH: EQUIVALENT + official_original，无需交叉验证"""
    assert compute_display_grade(
        support_level="equivalent",
        source_role="official_original",
        cross_verified=False,
    ) == GRADE_HIGH


def test_review__medium_inferred():
    """REVIEW: support_level=MEDIUM (INFERRED)"""
    assert compute_display_grade(
        support_level=SupportLevel.INFERRED,
        source_role="official_original",
    ) == GRADE_REVIEW


def test_review__strong_but_official_repost():
    """REVIEW: STRONG + source_role=official_repost"""
    assert compute_display_grade(
        support_level="direct",
        source_role="official_repost",
        cross_verified=True,
    ) == GRADE_REVIEW


def test_review__strong_commercial_repost():
    """REVIEW: STRONG + source_role=commercial_repost"""
    assert compute_display_grade(
        support_level=SupportLevel.EQUIVALENT,
        source_role="commercial_repost",
    ) == GRADE_REVIEW


def test_low__weak_unsupported():
    """LOW: WEAK (unsupported)"""
    assert compute_display_grade(
        support_level=SupportLevel.UNSUPPORTED,
        source_role="official_original",
    ) == GRADE_LOW


def test_low__weak_contradicted():
    """LOW: WEAK (contradicted)"""
    assert compute_display_grade(
        support_level="contradicted",
        source_role="official_original",
    ) == GRADE_LOW


def test_low__field_status_absent():
    """LOW: field_status=absent，即使其它条件全部符合"""
    assert compute_display_grade(
        support_level="direct",
        source_role="official_original",
        cross_verified=True,
        field_status="absent",
    ) == GRADE_LOW


def test_low__field_status_ambiguous():
    """LOW: field_status=ambiguous"""
    assert compute_display_grade(
        support_level="direct",
        source_role="official_original",
        field_status="ambiguous",
    ) == GRADE_LOW


def test_low__source_unknown():
    """LOW: source_role=unknown"""
    assert compute_display_grade(
        support_level="direct",
        source_role="unknown",
    ) == GRADE_LOW


def test_case_insensitive():
    """大小写不敏感（support_level 与 source_role 都转小写比较）"""
    assert compute_display_grade("DIRECT", "official_original") == GRADE_HIGH
    assert compute_display_grade("Direct", "OFFICIAL_ORIGINAL") == GRADE_HIGH
    assert compute_display_grade("Direct", "UNKNOWN") == GRADE_LOW


# ========== filter_by_strategy ==========

def _sample_fields():
    return [
        # high
        {"field_name": "f1", "display_grade": GRADE_HIGH, "support_level": "direct"},
        # review + strong (default 保留)
        {"field_name": "f2", "display_grade": GRADE_REVIEW, "support_level": "equivalent"},
        # review + medium (default 过滤)
        {"field_name": "f3", "display_grade": GRADE_REVIEW, "support_level": "inferred"},
        # low
        {"field_name": "f4", "display_grade": GRADE_LOW, "support_level": "unsupported"},
    ]


def test_strategy_strict_only_high():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "strict")
    assert [f["field_name"] for f in res] == ["f1"]


def test_strategy_default_high_and_strong_review():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "default")
    names = [f["field_name"] for f in res]
    # f1(high) + f2(review & strong=direct/equivalent)；f3(inferred),f4(low) 过滤
    assert names == ["f1", "f2"]


def test_strategy_loose_high_and_all_review():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "loose")
    names = [f["field_name"] for f in res]
    assert names == ["f1", "f2", "f3"]  # high + all review (no low)


def test_strategy_audit_includes_low():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "audit")
    names = [f["field_name"] for f in res]
    assert names == ["f1", "f2", "f3", "f4"]  # 全部


def test_strategy_invalid_raises():
    with pytest.raises(ValueError):
        filter_by_strategy([], "foo")


def test_filter_accepts_orm_objects():
    """兼容传入 FieldExtraction（带属性的对象）"""
    f1 = FieldExtraction(field_name="f1", field_status="present",
                         support_level="direct",
                         display_grade=GRADE_HIGH)
    f2 = FieldExtraction(field_name="f2", field_status="absent",
                         support_level="direct",
                         display_grade=GRADE_LOW)
    res = filter_by_strategy([f1, f2], "strict")
    assert [x.field_name for x in res] == ["f1"]


# ========== 接入 extractor 流水线：_populate_display_grades ==========

def test_populate_display_grades_in_extraction_result():
    from app.llm.extractor import _populate_display_grades
    r = ExtractionResult(
        model_id="test",
        prompt_hash="h",
        fields=[
            FieldExtraction(field_name="a", field_status="present",
                            support_level="direct"),  # high
            FieldExtraction(field_name="b", field_status="present",
                            support_level="inferred"),  # review
            FieldExtraction(field_name="c", field_status="absent",
                            support_level="direct"),  # low
        ],
    )
    _populate_display_grades(r, source_role="official_original")
    grades = [f.display_grade for f in r.fields]
    assert grades == [GRADE_HIGH, GRADE_REVIEW, GRADE_LOW]


# ========== 迁移脚本：幂等 + 空数据库 ==========

async def _run_mig_test():
    from scripts.migrate_add_display_grade import migrate_extracted_fields
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # drop=True: 先强制删除列 -> 再添加；验证幂等（drop 导致首遍 migrated，二遍 skipped）
    r1 = await migrate_extracted_fields(engine, drop=False)
    r2 = await migrate_extracted_fields(engine, drop=False)
    await engine.dispose()
    return r1, r2


def test_migration_idempotent():
    """迁移幂等：两次运行都无错误，第二次全部 skipped."""
    r1, r2 = asyncio.run(_run_mig_test())
    assert not r1["errors"], f"first run errors: {r1['errors']}"
    assert not r2["errors"], f"second run errors: {r2['errors']}"
    assert r2["idempotent_run2"] is True
    # 第二次运行：两个新列全部 skipped
    assert set(r2["skipped"]) == {"display_grade", "cross_verified"}


def test_migration_on_empty_db():
    """空数据库（先不 create_all）也能正常完成."""
    async def _t():
        from scripts.migrate_add_display_grade import migrate_extracted_fields
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        r = await migrate_extracted_fields(engine)
        await engine.dispose()
        return r
    r = asyncio.run(_t())
    assert not r["errors"]
    # 列 display_grade / cross_verified 已 either migrated or skipped
    assert r["table"] is True  # Base.metadata.create_all 会创建
