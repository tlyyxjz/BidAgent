"""LLM 意图解析模块。

将自然语言查询解析为结构化过滤条件 ParsedFilters。

工程规范：
- 所有 LLM 调用 async/await
- 失败时降级到关键词+正则兜底
- 日志带 request_id 上下文
"""
