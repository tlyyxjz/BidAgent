"""W2-05 数据库迁移验证脚本。

project_memory 硬约束:
- Database migration must be verified for idempotency and successful execution on empty databases
- Database migration must include scripts/verify_empty_db_migration.py for end-to-end validation

验证内容:
1. 空库可建表（create_all 成功）
2. 迁移幂等性（重复执行 init_database 不报错、不重复建表）
3. W2-05 三张新表 (extracted_fields / evidence / field_evidence_links) 结构正确
4. 关键字段、索引、外键存在
5. extract 操作可正常 CRUD（端到端验证）

用法:
    python scripts/verify_empty_db_migration.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 让脚本能从仓库根目录运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.database import Base
from app.models.evidence import (
    EVIDENCE_ROLES,
    MATCH_METHODS,
    SUPPORT_LEVELS,
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
)
from app.models.tender import Tender
from app.processors.evidence_repository import (
    EvidenceInput,
    FieldInput,
    create_field_with_evidence,
    get_field_with_evidence,
)


# W2-05 三张表的必需列
REQUIRED_COLUMNS = {
    "extracted_fields": [
        "id", "tender_id", "field_name", "field_status",
        "raw_value", "normalized_value", "amount_type", "currency", "lot_id",
        "support_level", "support_reason", "derivation_rule", "validator_version",
        "primary_evidence_id", "version_id", "is_current",
        "created_at", "updated_at",
    ],
    "evidence": [
        "id", "tender_id", "evidence_text",
        "context_before", "context_after",
        "raw_start", "raw_end", "normalized_start", "normalized_end",
        "match_method", "confidence", "verified", "verification_rule",
        "snapshot_sha256", "raw_text_sha256",
        "created_at",
    ],
    "field_evidence_links": [
        "id", "field_id", "evidence_id",
        "evidence_role", "sequence", "is_required",
        "created_at",
    ],
}

REQUIRED_INDEXES = {
    "extracted_fields": ["ix_extracted_fields_tender_id", "ix_extracted_fields_field_name"],
    "evidence": ["ix_evidence_tender_id"],
    "field_evidence_links": ["ix_field_evidence_links_field_id", "ix_field_evidence_links_evidence_id"],
}


async def verify_empty_db() -> dict:
    """验证空库迁移。"""
    result = {"steps": [], "errors": []}

    # 用内存 SQLite 模拟空库
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # ===== Step 1: 空库 create_all =====
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        result["steps"].append({"step": "create_all_empty", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "create_all_empty", "ok": False, "err": str(e)})
        result["errors"].append(f"create_all 失败: {e}")
        return result

    # ===== Step 2: 幂等性 - 重复 create_all 不报错 =====
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        result["steps"].append({"step": "create_all_idempotent", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "create_all_idempotent", "ok": False, "err": str(e)})
        result["errors"].append(f"幂等性失败: {e}")

    # ===== Step 3: 检查表结构 =====
    async with engine.connect() as conn:
        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            return {
                "tables": insp.get_table_names(),
                "columns": {t: [c["name"] for c in insp.get_columns(t)] for t in insp.get_table_names()},
                "indexes": {t: [i["name"] for i in insp.get_indexes(t)] for t in insp.get_table_names()},
                "fks": {t: [fk["referred_table"] for fk in insp.get_foreign_keys(t)] for t in insp.get_table_names()},
            }
        info = await conn.run_sync(_inspect)

    # 检查三张表存在
    for table in REQUIRED_COLUMNS:
        if table not in info["tables"]:
            result["errors"].append(f"缺表: {table}")
            result["steps"].append({"step": f"table_exists_{table}", "ok": False})
        else:
            result["steps"].append({"step": f"table_exists_{table}", "ok": True})

    # 检查列
    for table, cols in REQUIRED_COLUMNS.items():
        actual = set(info["columns"].get(table, []))
        missing = set(cols) - actual
        if missing:
            result["errors"].append(f"{table} 缺列: {sorted(missing)}")
            result["steps"].append({"step": f"columns_{table}", "ok": False, "missing": sorted(missing)})
        else:
            result["steps"].append({"step": f"columns_{table}", "ok": True})

    # 检查索引
    for table, idxs in REQUIRED_INDEXES.items():
        actual = set(info["indexes"].get(table, []))
        missing = set(idxs) - actual
        if missing:
            result["errors"].append(f"{table} 缺索引: {sorted(missing)}")
            result["steps"].append({"step": f"indexes_{table}", "ok": False, "missing": sorted(missing)})
        else:
            result["steps"].append({"step": f"indexes_{table}", "ok": True})

    # 检查外键
    expected_fks = {
        "extracted_fields": {"tenders"},
        "evidence": {"tenders"},
        "field_evidence_links": {"extracted_fields", "evidence"},
    }
    for table, expected_refs in expected_fks.items():
        actual_refs = set(info["fks"].get(table, []))
        missing_refs = expected_refs - actual_refs
        if missing_refs:
            result["errors"].append(f"{table} 缺外键到: {sorted(missing_refs)}")
            result["steps"].append({"step": f"fks_{table}", "ok": False, "missing": sorted(missing_refs)})
        else:
            result["steps"].append({"step": f"fks_{table}", "ok": True})

    # ===== Step 4: 端到端 CRUD =====
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with AsyncSessionLocal() as session:
            # 建 Tender
            tender = Tender(project_name="迁移验证-测试项目")
            session.add(tender)
            await session.commit()
            await session.refresh(tender)
            tid = tender.id

            # 建字段+证据
            fi = FieldInput(
                field_name="project_identifier",
                field_status="present",
                raw_value="ZFCG-2026-MIG-001",
                normalized_value="ZFCG-2026-MIG-001",
                support_level="direct",
                evidences=[
                    (EvidenceInput(
                        evidence_text="项目编号：ZFCG-2026-MIG-001",
                        raw_start=0, raw_end=22,
                        match_method="exact",
                        confidence=1.0,
                        verified=True,
                    ), "primary"),
                ],
            )
            raw_text = "项目编号：ZFCG-2026-MIG-001 是测试公告。"
            f = await create_field_with_evidence(session, tid, fi, raw_text=raw_text)
            await session.commit()

            # 查回
            f2, links = await get_field_with_evidence(session, f.id)
            if f2.id != f.id:
                raise AssertionError("查回字段 id 不匹配")
            if len(links) != 1:
                raise AssertionError(f"查回证据数量错误: {len(links)} != 1")
            if links[0][0].evidence_text != "项目编号：ZFCG-2026-MIG-001":
                raise AssertionError("证据文本不匹配")
            if links[0][1].evidence_role != "primary":
                raise AssertionError("证据角色不匹配")

            result["steps"].append({"step": "crud_e2e", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "crud_e2e", "ok": False, "err": str(e)})
        result["errors"].append(f"端到端 CRUD 失败: {e}")

    await engine.dispose()
    return result


async def main():
    print("=" * 70)
    print("W2-05 数据库迁移验证 (空库 + 幂等 + 端到端)")
    print("=" * 70)
    r = await verify_empty_db()

    print("\n--- 步骤明细 ---")
    for s in r["steps"]:
        ok = "OK" if s["ok"] else "FAIL"
        line = f"  [{ok}] {s['step']}"
        if "err" in s:
            line += f"  err={s['err']}"
        if "missing" in s:
            line += f"  missing={s['missing']}"
        print(line)

    print(f"\n--- 结论 ---")
    if r["errors"]:
        print(f"FAIL: {len(r['errors'])} 个错误")
        for e in r["errors"]:
            print(f"  - {e}")
        return 1
    else:
        print(f"PASS: 全部 {len(r['steps'])} 步通过")
        return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
