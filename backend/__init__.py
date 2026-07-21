"""BidAgent v4.1 后端模块。

按 v4.1 执行定稿版分工，GLM 5.2 主责：
- backend/models.py   四层数据模型（10 个实体）
- backend/schemas.py  标注 JSON Schema（Pydantic v2）
- backend/extractors.py  Direct LLM Baseline
- backend/evaluation.py  评测脚本（Precision/Recall/F1/空值误报率）
"""
