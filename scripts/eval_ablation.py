"""W2-08/W4 消融实验 A/B/C/D 四组。

对应总规划 v4.1 第十章 10.8 消融实验设计 + 第二周任务清单 W2-08。

四组对比:
- A 组 (Direct LLM): 直接输出字段，无证据要求
- B 组 (LLM + 候选证据): LLM 输出字段 + 候选证据文本，但不做程序验证
- C 组 (LLM + 程序证据验证): 验证证据存在性 + 字段等价性 + 确定性字段校验
- D 组 (完整 BidAgent): C 组验证 + display_grade 选择性输出 (grade="low" 拒绝)
  D 组在 C 组验证结果基础上计算 display_grade (v4.1 第八章)，
  按选择性输出策略 (v4.1 第十章 10.7) 拒绝 grade="low" 的字段，
  模拟完整 BidAgent 的来源处理 + 版本/冲突处理 + 选择性输出能力。

四组使用相同底层模型、相同公告文本、相同字段定义、相同提示词主体。

输出对比报告:
- 无依据输出率 (lower is better)
- 字段 P/R/F1 (vs 金标)
- 证据精确率 (higher is better)

约束 (project_memory):
- 真实 LLM 调用，记录模型标识/参数/token/延迟
- 不得使用测试集（W4 才冻结）
- 实验记录模型标识、参数、数据版本、代码提交版本
- 真实数据，未达到目标时如实报告

用法:
    python scripts/eval_ablation.py [--docs 7] [--skip-llm]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2")
RAW_DIR = WORK_DIR / "_w2_raw"
ANNOT_DIR = WORK_DIR / "_w2_annotations"

# W3 数据源路径
BIDAGENT_ROOT = Path(r"C:\Users\Lenovo\Desktop\BidAgent")
W3_RAW_DIR = BIDAGENT_ROOT / "_w3_raw"
W3_GOLD_PATH = BIDAGENT_ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
W3_OUTPUT_DIR = BIDAGENT_ROOT / "_w3_outputs"

from app.llm.extractor import (
    call_extraction_llm,
    call_extraction_llm_no_evidence,
    compute_prompt_hash,
    EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
    EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
)
from app.llm.extraction_schemas import CORE_FIELD_NAMES, ExtractionResult, FieldExtraction
from app.processors.evidence_locator import EvidenceLocator
from app.processors.field_validator import (
    validate_amount, validate_date, validate_project_identifier,
    ValidationResult,
)
from app.processors.display_grade import compute_display_grade
from app.eval.set_metrics import (
    compute_multi_value_field_metrics,
    is_multi_value_field,
)
from scripts.experiment_meta import collect_experiment_meta


# 7 篇金标 (W1 已有 + W2 D2 验证过的)
DEFAULT_DOCS = [
    "tender_06",
    "tender_07",
    "award_05",
    "award_06",
    "correction_04",
    "correction_05",
    "multi_lot_02",
]


@dataclass
class GoldField:
    field_name: str
    gold_status: str  # v4.1 §10.3: present/absent/not_applicable/ambiguous/attachment_only/unreadable
    values: list  # [{"raw_value": ..., "acceptable_evidence_spans": [{"start","end","text"}]}]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    fields: list  # [GoldField]


@dataclass
class GroupResult:
    group: str  # A/B/C
    doc_id: str
    field_name: str
    gold_status: str
    pred_status: str  # present/absent/ambiguous/multi_value/missing
    has_value: bool  # 系统是否输出了值
    has_evidence: bool  # 系统是否输出证据 (B/C 才有)
    evidence_verified: bool  # 证据是否在原文中存在 (C 才有)
    field_validated: bool  # 字段是否通过确定性校验 (C 才有)
    unjustified: bool  # 无依据输出 (有值但无证据/证据不存在)
    correct: Optional[bool]  # 字段值是否与金标一致 (None=无法判断)
    multi_value_f1: Optional[float] = None  # v4.1 sec 7.4 多值字段集合级 F1 (None=非多值字段)


@dataclass
class ExpSummary:
    group: str
    docs_count: int
    fields_total: int
    fields_with_value: int
    fields_with_evidence: int
    fields_evidence_verified: int
    fields_field_validated: int
    fields_unjustified: int
    unjustified_rate: float
    fields_correct: int
    fields_evaluable: int
    field_precision: float
    evidence_precision: float  # C 组字段级证据验证率 (已验证证据字段 / 有证据字段)
    model_id: str
    prompt_hash: str
    total_tokens: int
    latency_ms_avg: float
    invalid_docs_count: int = 0  # P2: LLM 失败被排除的文档数
    invalid_docs: list = field(default_factory=list)  # P2: 失败文档 ID 列表
    multi_value_f1_avg: float = 0.0  # v4.1 sec 7.4 多值字段平均集合级 F1
    null_false_positive_rate: float = 0.0  # v4.1 §10 空值误报率（should_not_have_value 字段中系统错误输出值的比例）
    # ==== v4.1 §10.12 实验复现信息（14 项新增，prompt_hash/model_id 已有）====
    model_role: str = "primary"
    provider: str = "deepseek"
    model_snapshot: Optional[str] = None
    request_time: str = ""
    temperature: float = 0.0
    top_p: float = 1.0
    seed: Optional[int] = None
    request_id: Optional[str] = None
    response_hash: Optional[str] = None
    normalizer_version: str = "unknown"
    evidence_rule_version: str = "unknown"
    display_rule_version: str = "unknown"
    dataset_version: Optional[str] = None
    code_commit: Optional[str] = None


def load_gold_all_w3() -> list[GoldDoc]:
    """从 W3 gold JSON (k3_annotations_batch2.json) 加载全部金标。

    JSON 结构: 顶部一个 _is_meta 头, 其余 99 项为公告金标。
    """
    with open(W3_GOLD_PATH, encoding="utf-8") as f:
        data = json.load(f)
    docs = []
    for item in data:
        if not isinstance(item, dict) or item.get("_is_meta"):
            continue
        fields = [
            GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                values=f.get("values", []),
            )
            for f in item.get("fields", [])
        ]
        docs.append(GoldDoc(
            document_id=item["document_id"],
            file=item.get("file", ""),
            fields=fields,
        ))
    return docs


_GOLD_CACHE_W3: dict[str, GoldDoc] = {}


def _load_w3_gold_cache() -> dict[str, GoldDoc]:
    """惰性加载 W3 gold 到缓存 (按 document_id 索引)。"""
    if not _GOLD_CACHE_W3:
        for gd in load_gold_all_w3():
            _GOLD_CACHE_W3[gd.document_id] = gd
    return _GOLD_CACHE_W3


def load_gold_doc(doc_prefix: str, source: str = "w2") -> Optional[GoldDoc]:
    """加载金标。

    source="w2" 从 _w2_annotations 目录按 prefix glob 查找单独 JSON 文件;
    source="w3" 从 k3_annotations_batch2.json 按 document_id 直接匹配
    (W3 的 doc_prefix 就是 document_id, 如 w3_tender_001)。
    """
    if source == "w3":
        cache = _load_w3_gold_cache()
        return cache.get(doc_prefix)
    matches = list(ANNOT_DIR.glob(f"annotation_{doc_prefix}*.json"))
    if not matches:
        return None
    with open(matches[0], encoding="utf-8") as f:
        data = json.load(f)
    return GoldDoc(
        document_id=data["document_id"],
        file=matches[0].name,
        fields=[
            GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                values=f.get("values", []),
            )
            for f in data["fields"]
        ],
    )


def load_raw_text(doc_prefix: str, source: str = "w2") -> Optional[str]:
    """加载原文。source="w3" 从 _w3_raw, 否则从 _w2_raw。"""
    if source == "w3":
        p = W3_RAW_DIR / f"{doc_prefix}.txt"
    else:
        p = RAW_DIR / f"{doc_prefix}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _status_matches(gold: str, pred: str) -> bool:
    """字段状态匹配判定。"""
    if gold == pred:
        return True
    # absent vs present/ambiguous 都算不匹配
    # multi_value 视为 present 的特殊形式
    if gold == "multi_value" and pred == "present":
        return True
    return False


def _extract_amount_unit(raw: str) -> str | None:
    """从金额字符串中提取单位 (元/万元/亿元)。"""
    if not raw:
        return None
    if "亿元" in raw or "亿" in raw:
        return "亿元"
    if "万元" in raw or "万" in raw:
        return "万元"
    if "元" in raw:
        return "元"
    return None


def _amount_correct(pred_raw: str, gold_raw: str) -> bool:
    """v4.1 sec 7.3 金额正确性判定 (基于显示精度的容差策略)。

    1. 用 validate_amount 把 pred 和 gold 都转换为元
    2. 用 _compute_tolerance_from_precision 计算容差 (基于 gold 单位推断)
    3. |pred_yuan - gold_yuan| <= tolerance -> 正确

    回退: 解析失败时退化为数字字符串精确匹配。
    """
    from app.processors.field_validator import (
        validate_amount,
        _compute_tolerance_from_precision,
    )

    pred_vr = validate_amount(pred_raw)
    gold_vr = validate_amount(gold_raw)
    if not pred_vr.valid or not gold_vr.valid:
        # 回退: 数字字符串精确匹配
        pred_num = re.findall(r"\d+\.?\d*", pred_raw)
        gold_num = re.findall(r"\d+\.?\d*", gold_raw)
        return bool(pred_num and gold_num and pred_num[0] == gold_num[0])

    pred_yuan = pred_vr.normalized_value or 0.0
    gold_yuan = gold_vr.normalized_value or 0.0

    # 容差: 优先用 gold 的 display_precision (金标通常无此字段, 这里为 None),
    # 退化到 original_unit 推断 (如 "万元" -> 50 元容差)
    gold_unit = _extract_amount_unit(gold_raw)
    tolerance = _compute_tolerance_from_precision(None, gold_unit)
    if tolerance is None:
        tolerance = 0.0  # 无法判定容差时, 要求精确相等

    diff = abs(pred_yuan - gold_yuan)
    return diff <= tolerance


def _classify_gold_status(gold_status: str) -> str:
    """将金标状态分类为评测口径。

    v4.1 §10.3 6 种金标状态分为 4 类评测口径：
    - "should_have_value": present/ambiguous/multi_value → 系统应输出值
    - "should_not_have_value": absent/not_applicable → 系统不应输出值
    - "attachment_only": 字段在附件中，正文抽取算无依据但不算错误
    - "unreadable": 无法判定，correct=None

    Args:
        gold_status: 金标状态（6 种之一）

    Returns:
        评测口径类别
    """
    if gold_status in ("present", "ambiguous", "multi_value"):
        return "should_have_value"
    if gold_status in ("absent", "not_applicable"):
        return "should_not_have_value"
    if gold_status == "attachment_only":
        return "attachment_only"
    if gold_status == "unreadable":
        return "unreadable"
    # 默认按 should_have_value 处理（向后兼容）
    return "should_have_value"


def _value_correct(field_name: str, pred_value: str, gold_field: GoldField) -> bool:
    """判断字段值是否与金标一致。

    - amount 字段: v4.1 sec 7.3 容差策略 (单位转换 + 显示精度容差)
    - 其他字段: 子串匹配 (容错：LLM 可能多带前后缀)
    """
    if not pred_value or not gold_field.values:
        return False
    pred = pred_value.strip()
    for v in gold_field.values:
        gold_v = (v.get("raw_value") or "").strip()
        if not gold_v:
            continue
        # amount 字段: 走 v4.1 sec 7.3 容差比较
        if field_name == "amount":
            if _amount_correct(pred, gold_v):
                return True
            continue
        # 其他字段: 子串匹配 (容错：LLM 可能多带前后缀)
        if gold_v in pred or pred in gold_v:
            return True
    return False


# ========== v4.1 sec 7.4 多值字段集合级评测辅助函数 ==========

def _collect_pred_values_by_name(fields: list) -> dict[str, list[str]]:
    """按 field_name 收集所有预测值（多值字段不被压平）。"""
    result: dict[str, list[str]] = {}
    for f in fields:
        if f.raw_value:
            result.setdefault(f.field_name, []).append(f.raw_value)
    return result


def _collect_gold_values(gold_field: GoldField) -> list[str]:
    """从 gold_field.values 收集所有金标值。"""
    values = []
    for v in gold_field.values:
        raw = v.get("raw_value")
        if raw:
            values.append(raw)
    return values


def _compute_mv_f1(field_name: str, pred_values: list[str], gold_field: GoldField) -> Optional[float]:
    """对多值字段计算集合级 F1，非多值字段返回 None。"""
    if not is_multi_value_field(field_name):
        return None
    gold_values = _collect_gold_values(gold_field)
    metrics = compute_multi_value_field_metrics(pred_values, gold_values)
    return round(metrics.f1, 4)


# ========== A 组：Direct LLM（直接输出字段，无证据要求）==========

async def run_group_a(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """A 组：直接调用 LLM，不要求证据（Sol 要求 W2-08 A 组用独立无证据 prompt）。

    修复 (P1)：原实现复用 call_extraction_llm (有证据 prompt) 仅评测时忽略证据，
    导致 LLM 仍被要求输出证据，不符合 "Direct LLM 无证据要求" 的实验目的。
    现改用 call_extraction_llm_no_evidence，使用独立的无证据 prompt + few-shot。
    """
    result = await call_extraction_llm_no_evidence(raw_text)
    # A 组 LLM 失败检测（与 B 组一致）
    is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "invalid": is_invalid,
    }
    if is_invalid:
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    pred_values_by_name = _collect_pred_values_by_name(result.fields)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        # A 组不评证据 (无证据 prompt，candidate_evidences 必为空)
        pred_value = pred.raw_value if pred else ""
        # v4.1 §10.3: 金标状态分类判定
        gold_category = _classify_gold_status(gf.gold_status)
        if gold_category == "should_not_have_value":
            # absent/not_applicable: 系统不应输出值
            correct = (pred_status == "absent") if pred else True
        elif gold_category == "attachment_only":
            # attachment_only: 字段在附件中，正文抽取算无依据但不算值错误
            correct = None  # 无法判定，correct=None
        elif gold_category == "unreadable":
            # unreadable: 无法判定
            correct = None
        else:
            # should_have_value: 走原有值匹配逻辑
            correct = _value_correct(gf.field_name, pred_value, gf) if has_value else False
        # v4.1 sec 7.4: 多值字段集合级 F1
        mv_f1 = _compute_mv_f1(gf.field_name, pred_values_by_name.get(gf.field_name, []), gf)
        rows.append(GroupResult(
            group="A", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=False, evidence_verified=False,
            field_validated=False,
            # A 组无证据要求：有值即算无依据 (对比 B/C 组通过证据降低无依据率)
            unjustified=has_value,
            correct=correct,
            multi_value_f1=mv_f1,
        ))
    return rows, meta


# ========== B 组：LLM + 候选证据（不验证）==========

async def run_group_b(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """B 组：LLM 输出字段 + 候选证据，但不做程序验证。

    修复 (P2)：添加 LLM 失败错误检测。
    原实现 multi_lot_02 LLM 调用失败 (tokens=0) 被静默吞掉，6 字段全 missing
    被算入评测，导致 B 组 fields_evaluable=37 (A/C 都是 42) 数据失真。
    现检测 result.error / total_tokens==0 / fields 为空，标记 invalid 跳过评测。
    """
    result = await call_extraction_llm(raw_text)
    is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "invalid": is_invalid,
    }
    if is_invalid:
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    pred_values_by_name = _collect_pred_values_by_name(result.fields)
    locator = EvidenceLocator(raw_text)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        has_evidence = bool(pred and len(pred.candidate_evidences) > 0)
        # B 组不验证证据，evidence_verified=False
        pred_value = pred.raw_value if pred else ""
        # v4.1 §10.3: 金标状态分类判定
        gold_category = _classify_gold_status(gf.gold_status)
        if gold_category == "should_not_have_value":
            # absent/not_applicable: 系统不应输出值
            correct = (pred_status == "absent") if pred else True
        elif gold_category == "attachment_only":
            # attachment_only: 字段在附件中，正文抽取算无依据但不算值错误
            correct = None  # 无法判定，correct=None
        elif gold_category == "unreadable":
            # unreadable: 无法判定
            correct = None
        else:
            # should_have_value: 走原有值匹配逻辑
            correct = _value_correct(gf.field_name, pred_value, gf) if has_value else False
        # v4.1 sec 7.4: 多值字段集合级 F1
        mv_f1 = _compute_mv_f1(gf.field_name, pred_values_by_name.get(gf.field_name, []), gf)
        rows.append(GroupResult(
            group="B", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=has_evidence, evidence_verified=False,
            field_validated=False,
            unjustified=has_value and not has_evidence,
            correct=correct,
            multi_value_f1=mv_f1,
        ))
    return rows, meta


# ========== C 组：LLM + 程序证据验证 + 确定性字段校验 ==========

async def run_group_c(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """C 组：LLM 输出 + EvidenceLocator 验证 + FieldValidator 校验。

    修复 (P0-1)：原实现缺少 LLM 失败检测 (无 invalid 标志)，
    导致 main() 中 `if meta_c.get("invalid")` 分支为 dead path，
    即使 LLM 调用失败 (tokens=0/error) 仍按空字段参与评测。
    现对齐 run_group_a / run_group_b 的 invalid 检测逻辑：
    result.error / total_tokens==0 / fields 为空 时标记 invalid 跳过评测。
    """
    result = await call_extraction_llm(raw_text)
    # C 组 LLM 失败检测（与 A/B 组一致）
    is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "invalid": is_invalid,
    }
    if is_invalid:
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    pred_values_by_name = _collect_pred_values_by_name(result.fields)
    locator = EvidenceLocator(raw_text)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        has_evidence = bool(pred and len(pred.candidate_evidences) > 0)

        # 证据验证：候选证据能否在原文中定位
        evidence_verified = False
        if has_evidence and pred:
            for ce in pred.candidate_evidences:
                loc = locator.locate(ce.evidence_text, search_from=0)
                if loc.found and loc.location is not None:
                    evidence_verified = True
                    break

        # 字段校验：amount/date/project_identifier
        field_validated = False
        if has_value and pred:
            try:
                vr: ValidationResult = None
                if gf.field_name == "amount":
                    vr = validate_amount(pred.raw_value, None)
                elif gf.field_name == "publish_date" or gf.field_name == "bid_deadline":
                    vr = validate_date(pred.raw_value)
                elif gf.field_name == "project_identifier":
                    vr = validate_project_identifier(pred.raw_value)
                if vr is not None:
                    field_validated = vr.valid
                else:
                    field_validated = True  # 无校验规则的字段默认通过
            except Exception:
                field_validated = False

        pred_value = pred.raw_value if pred else ""
        # v4.1 §10.3: 金标状态分类判定
        gold_category = _classify_gold_status(gf.gold_status)
        if gold_category == "should_not_have_value":
            # absent/not_applicable: 系统不应输出值
            correct = (pred_status == "absent") if pred else True
        elif gold_category == "attachment_only":
            # attachment_only: 字段在附件中，正文抽取算无依据但不算值错误
            correct = None  # 无法判定，correct=None
        elif gold_category == "unreadable":
            # unreadable: 无法判定
            correct = None
        else:
            # should_have_value: 走原有值匹配逻辑
            correct = _value_correct(gf.field_name, pred_value, gf) if has_value else False
        # v4.1 sec 7.4: 多值字段集合级 F1
        mv_f1 = _compute_mv_f1(gf.field_name, pred_values_by_name.get(gf.field_name, []), gf)
        rows.append(GroupResult(
            group="C", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=has_evidence,
            evidence_verified=evidence_verified, field_validated=field_validated,
            # C 组：有值但 (无证据 OR 证据未验证 OR 字段未通过校验) 算无依据
            unjustified=has_value and (not evidence_verified or not field_validated),
            correct=correct,
            multi_value_f1=mv_f1,
        ))
    return rows, meta


# ========== D 组：完整 BidAgent（C 组验证 + display_grade 选择性输出）==========

async def run_group_d(rows_c: list[GroupResult], meta_c: dict) -> tuple[list[GroupResult], dict]:
    """D 组：完整 BidAgent = C 组验证 + display_grade 选择性输出。

    直接复用 C 组的 LLM 调用与证据验证结果 (不重新调用 LLM)，
    在 C 组结果基础上计算 display_grade (v4.1 第八章)，
    并按选择性输出策略 (v4.1 第十章 10.7) 拒绝 grade="low" 的字段。

    Baseline 公平性 (v4.1 10.11): D 组与 C 组使用相同 LLM 调用结果，
    仅在 C 组验证后增加 display_grade 计算和选择性输出，
    确保 "相同重试次数" 和 "相同 LLM 调用" 的公平性约束。

    与 C 组的差异:
    - 计算 display_grade: support_level 基于 evidence_verified + field_validated
      (direct=STRONG / inferred=MEDIUM / unsupported=WEAK)
    - 选择性输出: grade="low" 的字段被拒绝 (不输出)
    - unjustified = 有值但被拒绝 (has_value and grade=="low")
    - correct 只统计输出字段 (grade != "low")，被拒绝字段 correct=None

    单源评测默认 source_role="official_original"，cross_verified=False (W3 无多源)。

    meta 中额外记录 display_grade 分布 (high/review/low 计数)。
    D 组 tokens/latency 复用 C 组 (不重新调用 LLM)。
    """
    if meta_c.get("invalid"):
        return [], meta_c

    rows_d: list[GroupResult] = []
    grade_dist = {"high": 0, "review": 0, "low": 0}
    for r in rows_c:
        # 基于 C 组结果计算 display_grade
        if r.evidence_verified and r.field_validated:
            support_level = "direct"  # STRONG
        elif r.has_value:
            support_level = "inferred"  # MEDIUM
        else:
            support_level = "unsupported"  # WEAK
        source_role = "official_original"  # 单源评测默认
        cross_verified = False  # W3 无多源交叉验证
        field_status = r.pred_status  # 已是 pred_status if pred else "missing"
        grade = compute_display_grade(support_level, source_role, cross_verified, field_status)
        grade_dist[grade] += 1

        # D 组选择性输出: grade="low" 被拒绝 (不输出)
        output = grade != "low"
        # unjustified: 有值但被拒绝
        unjustified = r.has_value and not output
        # correct: 只统计输出字段 (被拒绝字段不计入 evaluable)
        correct = r.correct if output else None

        rows_d.append(GroupResult(
            group="D", doc_id=r.doc_id, field_name=r.field_name,
            gold_status=r.gold_status, pred_status=r.pred_status,
            has_value=r.has_value, has_evidence=r.has_evidence,
            evidence_verified=r.evidence_verified, field_validated=r.field_validated,
            unjustified=unjustified,
            correct=correct,
            multi_value_f1=r.multi_value_f1,  # v4.1 sec 7.4: 透传 C 组多值 F1
        ))

    # 独立记录 meta (添加 display_grade 分布)
    meta_d = dict(meta_c)
    meta_d["display_grade_dist"] = grade_dist
    return rows_d, meta_d

# ========== 汇总 ==========

def summarize(
    group: str,
    all_rows: list[GroupResult],
    metas: list[dict],
    invalid_docs: list[str] = None,
) -> ExpSummary:
    total = len(all_rows)
    with_value = sum(1 for r in all_rows if r.has_value)
    with_evidence = sum(1 for r in all_rows if r.has_evidence)
    ev_verified = sum(1 for r in all_rows if r.evidence_verified)
    f_validated = sum(1 for r in all_rows if r.field_validated)
    unjustified = sum(1 for r in all_rows if r.unjustified)
    correct = sum(1 for r in all_rows if r.correct is True)
    # v4.1 §10.3: unreadable/attachment_only 状态不计入可评测字段 (correct=None)
    evaluable = sum(1 for r in all_rows if r.correct is not None)
    invalid_docs = invalid_docs or []

    # v4.1 sec 7.4: 多值字段平均集合级 F1
    mv_f1s = [r.multi_value_f1 for r in all_rows if r.multi_value_f1 is not None]
    mv_f1_avg = round(sum(mv_f1s) / max(len(mv_f1s), 1), 4) if mv_f1s else 0.0

    # v4.1 §10: 空值误报率（should_not_have_value 字段中系统错误输出值的比例）
    should_not_have_value_fields = [r for r in all_rows if _classify_gold_status(r.gold_status) == "should_not_have_value"]
    null_false_positives = sum(1 for r in should_not_have_value_fields if r.has_value)
    null_false_positive_rate = round(
        null_false_positives / len(should_not_have_value_fields), 4
    ) if should_not_have_value_fields else 0.0

    # v4.1 §10.12 收集实验复现信息（16 项）
    _model_id = metas[0].get("model_id", "unknown") if metas else "unknown"
    _prompt_hash = metas[0].get("prompt_hash", "") if metas else ""
    meta = collect_experiment_meta(
        model_id=_model_id,
        prompt_hash=_prompt_hash,
        dataset_path=None,
    )

    return ExpSummary(
        group=group,
        docs_count=len({r.doc_id for r in all_rows}),
        fields_total=total,
        fields_with_value=with_value,
        fields_with_evidence=with_evidence,
        fields_evidence_verified=ev_verified,
        fields_field_validated=f_validated,
        fields_unjustified=unjustified,
        unjustified_rate=round(unjustified / max(with_value, 1), 4),
        fields_correct=correct,
        fields_evaluable=evaluable,
        field_precision=round(correct / max(evaluable, 1), 4),
        # C 组字段级证据验证率 (已验证证据字段 / 有证据字段)
        # 命名澄清 (P3): 此处是字段级，非证据级，与 W2-09 证据级精确率口径不同
        evidence_precision=round(ev_verified / max(with_evidence, 1), 4) if group in ("C", "D") else 0.0,
        model_id=_model_id,
        prompt_hash=_prompt_hash,
        total_tokens=sum(m.get("total_tokens", 0) for m in metas),
        latency_ms_avg=round(sum(m.get("latency_ms", 0) for m in metas) / max(len(metas), 1), 1),
        invalid_docs_count=len(invalid_docs),
        invalid_docs=invalid_docs,
        multi_value_f1_avg=mv_f1_avg,
        null_false_positive_rate=null_false_positive_rate,
        # ==== v4.1 §10.12 实验复现信息（14 项新增）====
        model_role=meta.model_role,
        provider=meta.provider,
        model_snapshot=meta.model_snapshot,
        request_time=meta.request_time,
        temperature=meta.temperature,
        top_p=meta.top_p,
        seed=meta.seed,
        request_id=meta.request_id,
        response_hash=meta.response_hash,
        normalizer_version=meta.normalizer_version,
        evidence_rule_version=meta.evidence_rule_version,
        display_rule_version=meta.display_rule_version,
        dataset_version=meta.dataset_version,
        code_commit=meta.code_commit,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", nargs="*", default=None, help="要跑的公告前缀列表 (w2) 或 document_id 子集 (w3)")
    parser.add_argument("--source", choices=["w2", "w3"], default="w2", help="数据源: w2=21篇W2标注, w3=99篇W3金标")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 调用 (用空结果)")
    parser.add_argument("--out", default=None, help="输出路径 (默认自动: w2->WORK_DIR/_w2_d4_ablation_result.json, w3->W3_OUTPUT_DIR/w3_ablation_full.json)")
    args = parser.parse_args()

    source = args.source

    # w3 模式: 从 gold JSON 动态读取全量 document_id; w2 模式: 用 DEFAULT_DOCS
    if source == "w3":
        all_gold = load_gold_all_w3()
        docs_to_run = [gd.document_id for gd in all_gold]
        if args.docs:
            wanted = set(args.docs)
            docs_to_run = [d for d in docs_to_run if d in wanted]
        out_path = args.out or str(W3_OUTPUT_DIR / "w3_ablation_full.json")
        W3_OUTPUT_DIR.mkdir(exist_ok=True)
    else:
        docs_to_run = args.docs if args.docs is not None else DEFAULT_DOCS
        out_path = args.out or str(WORK_DIR / "_w2_d4_ablation_result.json")

    print("=" * 70)
    print("W2-08/W4 消融实验 A/B/C/D 四组")
    print("=" * 70)
    print(f"数据源: {source}")
    print(f"公告数: {len(docs_to_run)}")
    print(f"模型: deepseek-v4-flash")
    print(f"prompt_hash: {compute_prompt_hash()}")
    print(f"输出: {out_path}")
    print()

    # 加载金标和原文
    docs: list[tuple[GoldDoc, str]] = []
    for prefix in docs_to_run:
        gd = load_gold_doc(prefix, source=source)
        rt = load_raw_text(prefix, source=source)
        if gd is None or rt is None:
            print(f"[WARN] 跳过 {prefix} (金标或原文缺失)")
            continue
        docs.append((gd, rt))
    print(f"实际加载: {len(docs)} 篇")

    if args.skip_llm:
        print("[SKIP-LLM] 跳过 LLM 调用，仅输出空结果")
        return

    all_rows_a, all_rows_b, all_rows_c, all_rows_d = [], [], [], []
    metas_a, metas_b, metas_c, metas_d = [], [], [], []
    invalid_a, invalid_b, invalid_c, invalid_d = [], [], [], []

    for gd, rt in docs:
        print(f"\n--- {gd.document_id} ({len(rt)} 字符) ---")
        # A 组
        rows_a, meta_a = await run_group_a(gd, rt)
        if meta_a.get("invalid"):
            print(f"  A: [INVALID] tokens={meta_a['total_tokens']}, error={meta_a['error']} - 跳过评测")
            invalid_a.append(gd.document_id)
            metas_a.append(meta_a)
        else:
            print(f"  A: {len(rows_a)} 字段, tokens={meta_a['total_tokens']}, latency={meta_a['latency_ms']}ms, error={meta_a['error']}")
            all_rows_a.extend(rows_a); metas_a.append(meta_a)
        # B 组
        rows_b, meta_b = await run_group_b(gd, rt)
        if meta_b.get("invalid"):
            print(f"  B: [INVALID] tokens={meta_b['total_tokens']}, error={meta_b['error']} - 跳过评测")
            invalid_b.append(gd.document_id)
            metas_b.append(meta_b)
        else:
            print(f"  B: {len(rows_b)} 字段, tokens={meta_b['total_tokens']}, latency={meta_b['latency_ms']}ms")
            all_rows_b.extend(rows_b); metas_b.append(meta_b)
        # C 组
        rows_c, meta_c = await run_group_c(gd, rt)
        if meta_c.get("invalid"):
            print(f"  C: [INVALID] tokens={meta_c['total_tokens']}, error={meta_c['error']} - 跳过评测")
            invalid_c.append(gd.document_id)
            metas_c.append(meta_c)
        else:
            print(f"  C: {len(rows_c)} 字段, tokens={meta_c['total_tokens']}, latency={meta_c['latency_ms']}ms")
            all_rows_c.extend(rows_c); metas_c.append(meta_c)
        # D 组 (复用 C 组 LLM 调用结果 + display_grade 选择性输出)
        # Baseline 公平性: D 组不重新调用 LLM，与 C 组共享同一次 LLM 调用
        if meta_c.get("invalid"):
            rows_d, meta_d = [], meta_c
            print(f"  D: [INVALID] 复用 C 组 invalid 状态 - 跳过评测")
            invalid_d.append(gd.document_id)
            metas_d.append(meta_d)
        else:
            rows_d, meta_d = await run_group_d(rows_c, meta_c)
            gd_dist = meta_d.get("display_grade_dist", {})
            print(f"  D: {len(rows_d)} 字段, tokens={meta_d['total_tokens']} (复用C组), latency={meta_d['latency_ms']}ms (复用C组), "
                  f"grade={gd_dist}")
            all_rows_d.extend(rows_d); metas_d.append(meta_d)

    # 汇总 (传入 invalid_docs 用于报告)
    sum_a = summarize("A", all_rows_a, metas_a, invalid_a)
    sum_b = summarize("B", all_rows_b, metas_b, invalid_b)
    sum_c = summarize("C", all_rows_c, metas_c, invalid_c)
    sum_d = summarize("D", all_rows_d, metas_d, invalid_d)

    # 打印 invalid docs 警告
    if invalid_a or invalid_b or invalid_c or invalid_d:
        print("\n" + "!" * 70)
        print("警告: 检测到 LLM 调用失败的 invalid docs (已排除出评测)")
        print(f"  A 组 invalid: {invalid_a}")
        print(f"  B 组 invalid: {invalid_b}")
        print(f"  C 组 invalid: {invalid_c}")
        print(f"  D 组 invalid: {invalid_d}")
        print("!" * 70)

    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)
    print(f"{'指标':<24} {'A组':>12} {'B组':>12} {'C组':>12} {'D组':>12}")
    print("-" * 80)
    print(f"{'字段总数':<24} {sum_a.fields_total:>12} {sum_b.fields_total:>12} {sum_c.fields_total:>12} {sum_d.fields_total:>12}")
    print(f"{'有值字段':<24} {sum_a.fields_with_value:>12} {sum_b.fields_with_value:>12} {sum_c.fields_with_value:>12} {sum_d.fields_with_value:>12}")
    print(f"{'有证据字段':<24} {sum_a.fields_with_evidence:>12} {sum_b.fields_with_evidence:>12} {sum_c.fields_with_evidence:>12} {sum_d.fields_with_evidence:>12}")
    print(f"{'证据已验证':<24} {sum_a.fields_evidence_verified:>12} {sum_b.fields_evidence_verified:>12} {sum_c.fields_evidence_verified:>12} {sum_d.fields_evidence_verified:>12}")
    print(f"{'字段已校验':<24} {sum_a.fields_field_validated:>12} {sum_b.fields_field_validated:>12} {sum_c.fields_field_validated:>12} {sum_d.fields_field_validated:>12}")
    print(f"{'无依据字段':<24} {sum_a.fields_unjustified:>12} {sum_b.fields_unjustified:>12} {sum_c.fields_unjustified:>12} {sum_d.fields_unjustified:>12}")
    print(f"{'无依据率':<24} {sum_a.unjustified_rate:>12.2%} {sum_b.unjustified_rate:>12.2%} {sum_c.unjustified_rate:>12.2%} {sum_d.unjustified_rate:>12.2%}")
    print(f"{'字段正确数':<24} {sum_a.fields_correct:>12} {sum_b.fields_correct:>12} {sum_c.fields_correct:>12} {sum_d.fields_correct:>12}")
    print(f"{'字段精确率':<24} {sum_a.field_precision:>12.2%} {sum_b.field_precision:>12.2%} {sum_c.field_precision:>12.2%} {sum_d.field_precision:>12.2%}")
    print(f"{'证据精确率':<24} {'N/A':>12} {'N/A':>12} {sum_c.evidence_precision:>12.2%} {sum_d.evidence_precision:>12.2%}")
    print(f"{'多值字段 F1':<24} {sum_a.multi_value_f1_avg:>12.4f} {sum_b.multi_value_f1_avg:>12.4f} {sum_c.multi_value_f1_avg:>12.4f} {sum_d.multi_value_f1_avg:>12.4f}")
    print(f"{'空值误报率':<24} {sum_a.null_false_positive_rate:>12.4f} {sum_b.null_false_positive_rate:>12.4f} {sum_c.null_false_positive_rate:>12.4f} {sum_d.null_false_positive_rate:>12.4f}")
    print(f"{'总 tokens':<24} {sum_a.total_tokens:>12} {sum_b.total_tokens:>12} {sum_c.total_tokens:>12} {sum_d.total_tokens:>12}")
    print(f"{'平均延迟 ms':<24} {sum_a.latency_ms_avg:>12.0f} {sum_b.latency_ms_avg:>12.0f} {sum_c.latency_ms_avg:>12.0f} {sum_d.latency_ms_avg:>12.0f}")

    # 保存
    out = {
        "summaries": {s.group: asdict(s) for s in [sum_a, sum_b, sum_c, sum_d]},
        "rows": {
            "A": [asdict(r) for r in all_rows_a],
            "B": [asdict(r) for r in all_rows_b],
            "C": [asdict(r) for r in all_rows_c],
            "D": [asdict(r) for r in all_rows_d],
        },
        "docs": [gd.document_id for gd, _ in docs],
    }
    final_out_path = Path(out_path)
    final_out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {final_out_path}")


if __name__ == "__main__":
    asyncio.run(main())
