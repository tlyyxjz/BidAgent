/**
 * Mock 示例数据（自动生成，请勿手动修改）
 * 权威来源：fixtures/sample-001.txt
 * 生成脚本：fixtures/generate.py
 * 仅用于演示和测试，不包含任何真实公告内容
 */

// ========== 示例原文（与 fixtures/sample-001.txt 完全一致） ==========
const SAMPLE_RAW_TEXT = "中华人民共和国政府采购网\n中标（成交）结果公告\n\n一、项目编号：ZFCG-2024-0315\n二、项目名称：市级政务云平台扩容升级项目\n三、中标（成交）信息\n\n供应商名称：上海智汇科技有限公司\n供应商地址：上海市浦东新区张江高科技园区博云路2号\n中标（成交）金额：1285.60万元\n\n四、主要标的信息\n\n序号：1\n名称：政务云平台扩容升级服务\n服务范围：包含计算资源、存储资源、网络安全等模块\n服务要求：满足等保三级要求，提供7×24小时运维支持\n服务时间：合同签订后12个月内完成部署，提供3年质保\n服务标准：符合国家政务信息化建设相关标准\n\n五、评审专家名单：\n王某、李某、张某、陈某、刘某\n\n六、代理服务收费标准及金额：\n收费标准：按国家计委计价格[2002]1980号文件规定\n收费金额：15.80万元\n\n七、公告期限\n自本公告发布之日起1个工作日。\n\n八、其他补充事宜\n无\n\n九、凡对本次公告内容提出询问，请按以下方式联系。\n\n1. 采购人信息\n名 称：某市大数据管理局\n地 址：某市行政中心3号楼\n联系方式：0576-88888888\n\n2. 采购代理机构信息\n名 称：某市政府采购中心\n地　址：某市公共资源交易中心五楼\n联系方式：0576-88889999\n\n3. 项目联系方式\n项目联系人：张工\n电　话：0576-88888888\n\n发布日期：2024年3月15日\n投标截止日期：2024年3月10日\n";

// ========== 示例标注数据（完整的 AnnotationDocument） ==========
const SAMPLE_ANNOTATION = {
    "document_id": "sample-001",
    "annotator_id": "A",
    "annotation_version": "1.0",
    "annotation_time": "2024-03-16T10:30:00+08:00",
    "fields": [
        {
            "field_name": "project_identifier",
            "gold_status": "present",
            "values": [
                {
                    "raw_value": "ZFCG-2024-0315",
                    "normalized_value": "ZFCG-2024-0315",
                    "amount_type": null,
                    "currency": null,
                    "original_unit": null,
                    "tax_status": null,
                    "lot_id": null,
                    "acceptable_evidence_spans": [
                        {
                            "role": "primary",
                            "start": 32,
                            "end": 46,
                            "text": "ZFCG-2024-0315"
                        }
                    ]
                }
            ],
            "note": ""
        },
        {
            "field_name": "purchaser_name",
            "gold_status": "present",
            "values": [
                {
                    "raw_value": "某市大数据管理局",
                    "normalized_value": "某市大数据管理局",
                    "amount_type": null,
                    "currency": null,
                    "original_unit": null,
                    "tax_status": null,
                    "lot_id": null,
                    "acceptable_evidence_spans": [
                        {
                            "role": "primary",
                            "start": 433,
                            "end": 441,
                            "text": "某市大数据管理局"
                        }
                    ]
                }
            ],
            "note": ""
        },
        {
            "field_name": "winner_name",
            "gold_status": "present",
            "values": [
                {
                    "raw_value": "上海智汇科技有限公司",
                    "normalized_value": "上海智汇科技有限公司",
                    "amount_type": null,
                    "currency": null,
                    "original_unit": null,
                    "tax_status": null,
                    "lot_id": null,
                    "acceptable_evidence_spans": [
                        {
                            "role": "primary",
                            "start": 86,
                            "end": 96,
                            "text": "上海智汇科技有限公司"
                        }
                    ]
                }
            ],
            "note": ""
        },
        {
            "field_name": "amount",
            "gold_status": "present",
            "values": [
                {
                    "raw_value": "1285.60万元",
                    "normalized_value": "12856000.00",
                    "amount_type": "award",
                    "currency": "CNY",
                    "original_unit": "万元",
                    "tax_status": "unknown",
                    "lot_id": null,
                    "acceptable_evidence_spans": [
                        {
                            "role": "primary",
                            "start": 132,
                            "end": 141,
                            "text": "1285.60万元"
                        },
                        {
                            "role": "qualifier",
                            "start": 123,
                            "end": 131,
                            "text": "中标（成交）金额"
                        }
                    ]
                }
            ],
            "note": "中标金额，人民币"
        },
        {
            "field_name": "publish_date",
            "gold_status": "present",
            "values": [
                {
                    "raw_value": "2024年3月15日",
                    "normalized_value": "2024-03-15",
                    "amount_type": null,
                    "currency": null,
                    "original_unit": null,
                    "tax_status": null,
                    "lot_id": null,
                    "acceptable_evidence_spans": [
                        {
                            "role": "primary",
                            "start": 581,
                            "end": 591,
                            "text": "2024年3月15日"
                        }
                    ]
                }
            ],
            "note": ""
        },
        {
            "field_name": "bid_deadline",
            "gold_status": "present",
            "values": [
                {
                    "raw_value": "2024年3月10日",
                    "normalized_value": "2024-03-10",
                    "amount_type": null,
                    "currency": null,
                    "original_unit": null,
                    "tax_status": null,
                    "lot_id": null,
                    "acceptable_evidence_spans": [
                        {
                            "role": "primary",
                            "start": 599,
                            "end": 609,
                            "text": "2024年3月10日"
                        }
                    ]
                }
            ],
            "note": ""
        }
    ]
};

// ========== 示例公告类型（根据原文推断，用于前端默认显示） ==========
// 不混入 SAMPLE_ANNOTATION（AnnotationDocument extra="forbid"）
// 仅作为前端 docMeta.noticeType 的默认值
const SAMPLE_NOTICE_TYPE = "award";

// 导出到 window
if (typeof window !== 'undefined') {
    window.SampleData = {
        SAMPLE_RAW_TEXT,
        SAMPLE_ANNOTATION,
        SAMPLE_NOTICE_TYPE
    };
}
