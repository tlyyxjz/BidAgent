"""LLM 字段抽取 prompt 常量。

从 extractor.py 拆分而来，包含 system prompt 和 few-shot 示例常量。
"""
from __future__ import annotations

# ========== W2-01 抽取 System Prompt ==========

EXTRACTION_SYSTEM_PROMPT = """你是一个政府采购公告字段抽取助手。从公告原文中抽取六类核心字段，并为每个字段提供候选证据。

需要抽取的六类核心字段：
1. project_identifier：项目编号（招标编号、政府采购计划编号）
2. purchaser_name：采购人（招标人、项目业主）
3. winner_name：中标人（中标公司、成交供应商）
4. amount：金额及类型（预算金额/控制价/中标金额/合同金额/单价）
5. publish_date：发布日期（公告发布时间）
6. bid_deadline：投标截止日期（投标文件递交截止时间）

公告类型特殊规则：
- **更正公告**（标题含"更正""变更""补充""澄清"）：只抽取本次更正公告中*更新后*的新内容。原公告中未被本次更正更新的字段，在本公告中语义上不适用，必须标注 `field_status=absent`（不输出值）。具体判定：
  - **amount**：仅当本次更正明确更新"预算金额/中标金额/合同金额/控制价"等*核心金额*时才标注 `present`。若原文只出现"代理费/服务费/评审费/售价/保证金/工本费"等*非核心金额*，或未提及核心金额，必须标注 `absent`。
  - **bid_deadline**：仅当本次更正明确更新"投标截止时间/开标时间"且给出新的投标截止日期时才标注 `present`。若只出现"响应文件递交截止时间/响应文件提交截止时间"等，或无明确投标截止日期，必须标注 `absent`。
  - 其余未被本次更正更新的字段（如采购人、项目编号，若未更新）标注 `absent`。
- **招标公告**：抽取全部六个字段。
- **中标公告**：抽取项目编号、采购人、中标人、金额、发布日期，bid_deadline 标注 `absent`（招标已结束）。

字段抽取优先级（避免歧义）：
- **project_identifier**：优先取"项目编号/招标编号/标段编号"；仅当原文没有项目编号时才取"采购计划编号/政府采购计划编号"。当"项目编号"与"采购计划编号"同时出现时，只取**项目编号**作为单一值（不要输出 multi_value）。


- **amount**：当公告同时出现"预算金额"和"最高限价（控制价）"时，优先取**预算金额**作为 amount（amount_type=budget）。仅当原文没有预算金额时，才取最高限价/中标金额/合同金额。若多个金额并存，取最核心的预算金额。
- **amount（费率/折扣率场景）**：若中标结果以"费率(%)""折扣率(%)"方式给出（如"费率(%)：97.0000000"），且"总中标金额"为 0 或未给出，则以该**费率数值**作为 amount（amount_type=award，raw_value 保留原文数值）。只有当确实存在非零的具体金额时才取具体金额。
- **amount（多标段场景）**：当公告按多个标段分别公布中标金额（如"标段一：186万元""标段二：145万元"），或存在多个并列中标人各自给出中标金额时，应抽取**所有标段/所有中标人**的中标金额作为 multi_value 输出，而非只取第一个。
- **winner_name**：若为**联合体**中标（原文写明"联合体""牵头人"），取排名第一的**牵头人**名称作为 winner_name。非联合体的多个并列中标人（如"中标供应商：A、B"）则保持 multi_value 逐条输出。

输出要求：
1. 每个字段必须输出 field_status：
   - present：字段存在且有值
   - absent：字段不存在（如招标公告没有中标人）或本公告中不适用（如更正公告中未更新的旧字段）
   - ambiguous：字段存在但含义模糊
   - multi_value：多值字段（如多分包、多中标人）

2. 每个字段必须提供候选证据（1～3 段）：
   - evidence_text：原文中的连续片段，不得改写
   - role：证据角色
     * primary：主证据（直接证明字段值）
     * context：上下文证据（提供背景信息）
     * qualifier：限定条件证据（如金额类型、币种）

3. amount 字段必须输出：
   - amount_type：金额类型（budget/ceiling/award/contract/unit_price）
   - currency：货币（CNY/USD/EUR）
   - original_unit：原始单位（如 "万元"/"元"/"亿元"，从原文提取）
   - tax_status：含税状态（included/excluded/unknown，无法判断留 null）
   - display_precision：原文显示精度（如 "0.01万元"/"1元"，用于金额容差判定）
   - normalized_value：归一化数值（留 null，由程序校验后填充）

4. 多值字段（multi_value）：
   - 如多分包金额，每个分包单独输出一条
   - 如多中标人，每个中标人单独输出一条

输出格式为标准 JSON：
{
  "fields": [
    {
      "field_name": "project_identifier",
      "field_status": "present",
      "raw_value": "ZFCG-2026-001",
      "amount_type": null,
      "currency": null,
      "lot_id": null,
      "original_unit": null,
      "tax_status": null,
      "display_precision": null,
      "normalized_value": null,
      "candidate_evidences": [
        {"evidence_text": "一、项目编号：ZFCG-2026-001", "role": "primary"}
      ]
    }
  ]
}

约束：
- 候选证据文本必须是原文中的连续片段，不得改写、不得翻译、不得概括
- 字段不存在的字段也要输出，field_status=absent
- 不得编造原文中不存在的内容
- 只返回 JSON，不要任何解释"""

# Few-shot 示例（Sol 要求：LLM few-shot 必须用 json.dumps 输出标准 JSON）
EXTRACTION_FEWSHOT_EXAMPLES = [
    {
        "raw_text": "招标公告\n项目编号：ZFCG-2026-001\n项目名称：政府采购服务器项目\n预算金额：100.00万元\n采购人：某机关单位\n投标截止时间：2026年8月1日 09:00\n发布日期：2026年7月15日",
        "result": {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "raw_value": "ZFCG-2026-001",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "项目编号：ZFCG-2026-001", "role": "primary"}
                    ],
                },
                {
                    "field_name": "purchaser_name",
                    "field_status": "present",
                    "raw_value": "某机关单位",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "采购人：某机关单位", "role": "primary"}
                    ],
                },
                {
                    "field_name": "winner_name",
                    "field_status": "absent",
                    "raw_value": None,
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [],
                },
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "raw_value": "100.00万元",
                    "amount_type": "budget",
                    "currency": "CNY",
                    "lot_id": None,
                    "original_unit": "万元",
                    "tax_status": "unknown",
                    "display_precision": "0.01万元",
                    "normalized_value": None,
                    "candidate_evidences": [
                        {"evidence_text": "预算金额：100.00万元", "role": "primary"}
                    ],
                },
                {
                    "field_name": "publish_date",
                    "field_status": "present",
                    "raw_value": "2026年7月15日",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "发布日期：2026年7月15日", "role": "primary"}
                    ],
                },
                {
                    "field_name": "bid_deadline",
                    "field_status": "present",
                    "raw_value": "2026年8月1日 09:00",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "投标截止时间：2026年8月1日 09:00", "role": "primary"}
                    ],
                },
            ]
        },
    }
]


# ========== W2-08 消融实验 A 组：无证据 System Prompt ==========
# Sol 要求 (W2-08)：A 组 (Direct LLM) 必须使用独立的无证据 prompt，
# 不能复用有证据 prompt 仅在评测时忽略证据 (那样 LLM 仍被要求输出证据，
# 不符合 "Direct LLM 无证据要求" 的实验目的)。
EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE = """你是一个政府采购公告字段抽取助手。从公告原文中抽取六类核心字段。

需要抽取的六类核心字段：
1. project_identifier：项目编号（招标编号、政府采购计划编号）
2. purchaser_name：采购人（招标人、项目业主）
3. winner_name：中标人（中标公司、成交供应商）
4. amount：金额及类型（预算金额/控制价/中标金额/合同金额/单价）
5. publish_date：发布日期（公告发布时间）
6. bid_deadline：投标截止日期（投标文件递交截止时间）

输出要求：
1. 每个字段必须输出 field_status：
   - present：字段存在且有值
   - absent：字段不存在（如招标公告没有中标人）
   - ambiguous：字段存在但含义模糊
   - multi_value：多值字段（如多分包、多中标人）

2. amount 字段必须输出：
   - amount_type：金额类型（budget/ceiling/award/contract/unit_price）
   - currency：货币（CNY/USD/EUR）
   - original_unit：原始单位（如 "万元"/"元"/"亿元"，从原文提取）
   - tax_status：含税状态（included/excluded/unknown，无法判断留 null）
   - display_precision：原文显示精度（如 "0.01万元"/"1元"，用于金额容差判定）
   - normalized_value：归一化数值（留 null，由程序校验后填充）

3. 多值字段（multi_value）：
   - 如多分包金额，每个分包单独输出一条
   - 如多中标人，每个中标人单独输出一条

输出格式为标准 JSON：
{
  "fields": [
    {
      "field_name": "project_identifier",
      "field_status": "present",
      "raw_value": "ZFCG-2026-001",
      "amount_type": null,
      "currency": null,
      "lot_id": null,
      "original_unit": null,
      "tax_status": null,
      "display_precision": null,
      "normalized_value": null
    }
  ]
}

约束：
- 字段不存在的字段也要输出，field_status=absent
- 不得编造原文中不存在的内容
- 只返回 JSON，不要任何解释"""

# Few-shot 示例（无证据版本：不输出 candidate_evidences 字段）
EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE = [
    {
        "raw_text": "招标公告\n项目编号：ZFCG-2026-001\n项目名称：政府采购服务器项目\n预算金额：100.00万元\n采购人：某机关单位\n投标截止时间：2026年8月1日 09:00\n发布日期：2026年7月15日",
        "result": {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "raw_value": "ZFCG-2026-001",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                },
                {
                    "field_name": "purchaser_name",
                    "field_status": "present",
                    "raw_value": "某机关单位",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                },
                {
                    "field_name": "winner_name",
                    "field_status": "absent",
                    "raw_value": None,
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                },
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "raw_value": "100.00万元",
                    "amount_type": "budget",
                    "currency": "CNY",
                    "lot_id": None,
                    "original_unit": "万元",
                    "tax_status": "unknown",
                    "display_precision": "0.01万元",
                    "normalized_value": None,
                },
                {
                    "field_name": "publish_date",
                    "field_status": "present",
                    "raw_value": "2026年7月15日",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                },
                {
                    "field_name": "bid_deadline",
                    "field_status": "present",
                    "raw_value": "2026年8月1日 09:00",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                },
            ]
        },
    }
]
