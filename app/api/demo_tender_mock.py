"""W3 Demo 招标字段 mock 数据。

提供：
- MOCK_RAW_TEXT：默认招标公告原文
- _build_mock_tender_fields：生成 mock 招标字段数据（无本地标注时使用）
"""

from __future__ import annotations

MOCK_RAW_TEXT = (
    "北京市政府采购中心\n"
    "医疗设备采购项目招标公告\n"
    "项目编号：BJGPC-2026-0042\n"
    "发布日期：2026年7月20日\n\n"
    "一、项目基本情况\n"
    "项目名称：信息化系统建设及医疗设备采购项目\n"
    "预算金额：人民币壹仟贰佰万元整（¥12,000,000.00）\n"
    "最高限价：1200万元\n"
    "采购需求：本项目采购信息化系统一套及配套医疗设备，具体详见招标文件。\n"
    "合同履行期限：合同签订后90天内完成供货、安装及调试。\n\n"
    "二、申请人的资格要求\n"
    "1. 满足《中华人民共和国政府采购法》第二十二条规定；\n"
    "2. 本项目不接受联合体投标。\n\n"
    "三、获取招标文件\n"
    "时间：2026年7月21日至2026年7月28日\n"
    "地点：北京市政府采购中心网站\n"
    "方式：在线下载\n"
    "售价：0元\n\n"
    "四、提交投标文件截止时间\n"
    "2026年8月15日 09点30分（北京时间）\n"
    "地点：北京市政府采购中心开标大厅\n\n"
    "五、公告期限\n"
    "自本公告发布之日起5个工作日。\n\n"
    "六、其他补充事宜\n"
    "本项目落实节约能源、保护环境等政府采购政策。\n\n"
    "七、对本次招标提出询问，请按以下方式联系\n"
    "1. 采购人信息\n"
    "名称：北京大学第三医院\n"
    "地址：北京市海淀区花园北路49号\n"
    "联系方式：010-82266699\n"
    "2. 采购代理机构信息\n"
    "名称：北京市政府采购中心\n"
    "地址：北京市丰台区玉林西路45号\n"
    "联系方式：010-63398900"
)


def _build_mock_tender_fields(tender_id: str) -> dict:
    """生成 mock 招标字段数据（无本地标注时使用）。"""
    fields = [
        {
            "field_id": "project_identifier",
            "field_name": "project_identifier",
            "field_label": "项目编号",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "project_identifier_0",
                "raw_value": "BJGPC-2026-0042",
                "normalized_value": "BJGPC-2026-0042",
                "evidences": [{
                    "id": "project_identifier_0_0",
                    "text": "BJGPC-2026-0042",
                    "start": 42,
                    "end": 58,
                    "role": "primary",
                    "match_method": "exact",
                    "confidence": 0.98,
                }],
            }],
        },
        {
            "field_id": "project_name",
            "field_name": "project_name",
            "field_label": "项目名称",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "project_name_0",
                "raw_value": "信息化系统建设及医疗设备采购项目",
                "normalized_value": "信息化系统建设及医疗设备采购项目",
                "evidences": [
                    {
                        "id": "project_name_0_0",
                        "text": "信息化系统建设及医疗设备采购项目",
                        "start": 84,
                        "end": 108,
                        "role": "primary",
                        "match_method": "exact",
                        "confidence": 0.95,
                    },
                    {
                        "id": "project_name_0_1",
                        "text": "医疗设备采购项目",
                        "start": 14,
                        "end": 24,
                        "role": "context",
                        "match_method": "exact",
                        "confidence": 0.8,
                    },
                ],
            }],
        },
        {
            "field_id": "purchaser_name",
            "field_name": "purchaser_name",
            "field_label": "采购人",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "purchaser_name_0",
                "raw_value": "北京大学第三医院",
                "normalized_value": "北京大学第三医院",
                "evidences": [
                    {
                        "id": "purchaser_name_0_0",
                        "text": "北京大学第三医院",
                        "start": 486,
                        "end": 495,
                        "role": "primary",
                        "match_method": "exact",
                        "confidence": 0.99,
                    },
                    {
                        "id": "purchaser_name_0_1",
                        "text": "采购人信息\n名称：北京大学第三医院",
                        "start": 480,
                        "end": 498,
                        "role": "context",
                        "match_method": "fuzzy",
                        "confidence": 0.85,
                    },
                ],
            }],
        },
        {
            "field_id": "amount",
            "field_name": "amount",
            "field_label": "金额",
            "support_level": "supported",
            "field_status": "present",
            "values": [
                {
                    "value_id": "amount_0",
                    "raw_value": "12,000,000.00",
                    "normalized_value": "12000000.00",
                    "amount_type": "budget",
                    "evidences": [
                        {
                            "id": "amount_0_0",
                            "text": "12,000,000.00",
                            "start": 109,
                            "end": 122,
                            "role": "primary",
                            "match_method": "exact",
                            "confidence": 0.97,
                        },
                        {
                            "id": "amount_0_1",
                            "text": "壹仟贰佰万元整",
                            "start": 100,
                            "end": 107,
                            "role": "qualifier",
                            "match_method": "exact",
                            "confidence": 0.92,
                        },
                    ],
                },
                {
                    "value_id": "amount_1",
                    "raw_value": "1200万元",
                    "normalized_value": "12000000.00",
                    "amount_type": "ceiling",
                    "evidences": [
                        {
                            "id": "amount_1_0",
                            "text": "1200万元",
                            "start": 129,
                            "end": 135,
                            "role": "primary",
                            "match_method": "exact",
                            "confidence": 0.95,
                        },
                        {
                            "id": "amount_1_1",
                            "text": "最高限价：1200万元",
                            "start": 124,
                            "end": 135,
                            "role": "context",
                            "match_method": "fuzzy",
                            "confidence": 0.88,
                        },
                    ],
                },
            ],
        },
        {
            "field_id": "publish_date",
            "field_name": "publish_date",
            "field_label": "发布日期",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "publish_date_0",
                "raw_value": "2026年7月20日",
                "normalized_value": "2026-07-20",
                "evidences": [{
                    "id": "publish_date_0_0",
                    "text": "2026年7月20日",
                    "start": 63,
                    "end": 73,
                    "role": "primary",
                    "match_method": "exact",
                    "confidence": 0.96,
                }],
            }],
        },
        {
            "field_id": "bid_deadline",
            "field_name": "bid_deadline",
            "field_label": "投标截止日期",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "bid_deadline_0",
                "raw_value": "2026年8月15日 09点30分",
                "normalized_value": "2026-08-15 09:30:00",
                "evidences": [
                    {
                        "id": "bid_deadline_0_0",
                        "text": "2026年8月15日 09点30分",
                        "start": 298,
                        "end": 316,
                        "role": "primary",
                        "match_method": "exact",
                        "confidence": 0.94,
                    },
                    {
                        "id": "bid_deadline_0_1",
                        "text": "提交投标文件截止时间\n2026年8月15日 09点30分",
                        "start": 285,
                        "end": 320,
                        "role": "context",
                        "match_method": "fuzzy",
                        "confidence": 0.82,
                    },
                ],
            }],
        },
        {
            "field_id": "winner_name",
            "field_name": "winner_name",
            "field_label": "中标人",
            "support_level": "unsupported",
            "field_status": "absent",
            "values": [],
        },
        {
            "field_id": "agency_name",
            "field_name": "agency_name",
            "field_label": "代理机构",
            "support_level": "unsupported",
            "field_status": "rejected",
            "values": [],
        },
    ]
    return {
        "tender_id": tender_id,
        "document_id": tender_id,
        "notice_type": "招标公告",
        "clean_raw_text": MOCK_RAW_TEXT,
        "fields": fields,
    }
