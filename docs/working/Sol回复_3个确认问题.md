# 给 Sol 的回复 — 3 个确认问题 + ZIP 包路径

## ZIP 包已上传到桌面

### 1. 当前项目 ZIP
- **路径**：`C:\Users\Lenovo\Desktop\bidagent_current.zip`
- **大小**：1.07 MB
- **文件数**：119
- **内容**：app/（agents/api/core/llm/models/processors/report/scheduler/templates/html/utils）+ tests/（12 个测试文件）+ docs/ + 配置文件（requirements.txt / pytest.ini / Dockerfile / docker-compose.yml / .env.example / .dockerignore / .gitignore）+ qianlima-dom.json
- **已排除**：.env / __pycache__ / .pytest_cache / data/ / *.db / *.sqlite / *.log / qianlima_session.json / qianlima-dom-probe*.json

### 2. 完整版 ZIP
- **路径**：`C:\Users\Lenovo\Desktop\bidagent_complete_v2.zip`
- **大小**：0.39 MB
- **文件数**：223
- **内容**：完整版 backend/ 全部代码（app/ + tests/ + tests_deep/ + cloudflare/ + docs/ + 配置文件）
- **已排除**：.env / __pycache__ / .pytest_cache / data/ / *.db / *.sqlite / *.log

---

## 3 个确认问题的回答

### Q1：S-4 是 7 个新模板还是确实有第 8 个？

**回答：7 个新模板文件 + 1 个 sources.py = 8 个文件**

我之前清单表述"8 个新模板"不准确，准确表述应该是：

| # | 文件名 | 内容 | 备注 |
|---|---|---|---|
| 1 | `bidcenter.py` | 中国采招网 | 单模板 |
| 2 | `cebpubservice.py` | 中国招标投标公共服务平台 | 单模板 |
| 3 | `ebnew.py` | 必联网 | 单模板 |
| 4 | `gdgpo.py` | 广东省政府采购网 + 通用 PROVINCE_ZFCG_TEMPLATE | 含通用模板 |
| 5 | `international.py` | UNGM / EU TED / India eTenders / WorldBank | **4 个国际模板** |
| 6 | `plap.py` | 军队采购网 | 单模板 |
| 7 | `province_zfcg.py` | 浙江/江苏/北京/四川/河南 | **5 省模板** |
| 8 | `sources.py` | 21 个 TenderSource 注册表 | 数据源注册表 |

所以是 **7 个模板文件（含 12 个具体模板）+ 1 个 sources.py 注册表 = 8 个文件**。

请按 7 个模板文件 + sources.py 实施，共 8 个文件。所有文件在完整版 ZIP 的 `app/templates/` 目录下。

---

### Q2：聊天 API 是否允许增加进度查询接口？

**回答：允许，且必须增加**

请按以下接口设计实施：

```
POST /chat/api              # 发起对话，返回 session_id + 第一阶段结果
GET  /chat/api/{session_id} # 轮询查询六阶段进度
```

#### POST /chat/api 请求/响应

**请求**：
```json
{
  "query": "找上海最近7天的IT采购项目",
  "session_id": null
}
```

**响应**（立即返回，stage="intent"）：
```json
{
  "session_id": "uuid-xxx",
  "parsed_filters": {
    "query": "IT采购",
    "region": "上海",
    "budget": null,
    "time_window": "最近7天",
    "category": "IT"
  },
  "collecting": [],
  "progress": 10,
  "stage": "intent",
  "message": "意图解析完成，开始采集",
  "result": null
}
```

#### GET /chat/api/{session_id} 响应

**进行中**（stage != "done"）：
```json
{
  "session_id": "uuid-xxx",
  "parsed_filters": {...},
  "collecting": ["ccgp", "chinabidding", "ggzy", "qianlima"],
  "progress": 45,
  "stage": "collecting",
  "message": "正在采集 4 个平台",
  "result": null
}
```

**完成**（stage == "done"）：
```json
{
  "session_id": "uuid-xxx",
  "parsed_filters": {...},
  "collecting": [...],
  "progress": 100,
  "stage": "done",
  "message": "报告已生成并发送邮件",
  "result": {
    "total_tenders": 15,
    "deduplicated": 12,
    "finance_analyzed": 12,
    "report_path": "/data/reports/上海IT采购_20260721.docx",
    "report_download_url": "/chat/download/uuid-xxx",
    "email_sent": true,
    "email_message_id": "<xxx@163.com>",
    "finance_summary": {
      "boq_anomalies": 3,
      "risk_items": 5,
      "supplier_scores": [{"name": "xxx", "score": 85, "risk_level": "low"}]
    }
  }
}
```

#### 六阶段 stage 值

| stage | progress | 说明 |
|---|---|---|
| `intent` | 10 | 意图解析完成 |
| `collecting` | 20-50 | 采集中（progress 随平台完成度递增） |
| `processing` | 60 | 数据加工完成 |
| `quality` | 70 | 质检完成（去重 + 反幻觉） |
| `finance` | 85 | 金融分析完成（BOQ + 废标 + 供应商） |
| `done` | 100 | 报告交付完成 |

#### 错误处理

- `session_id` 不存在：返回 404
- `query` 为空：返回 422
- LLM 不可用：降级到关键词匹配，stage="intent" 仍返回，`parsed_filters` 用规则匹配结果
- 采集失败：stage="collecting"，`collecting` 列表中标注失败平台

#### 实现建议

- session 数据存储在内存 dict（MVP 阶段足够，无需 Redis）
- 六 Agent 异步执行，每个阶段完成时更新 session dict
- 前端轮询 `GET /chat/api/{session_id}` 每 1 秒一次
- 可选：用 SSE（Server-Sent Events）替代轮询，但 MVP 阶段轮询足够

---

### Q3：是否必须新增 app/agents/ 层？

**回答：不强制 Sol 新增，agents 层由我自己（主对话）实现**

原因：
1. `app/agents/` 目录已存在（含 `coordinator.py` 轻量级 Agent 协作框架）
2. 六 Agent 接口骨架（`intent_agent.py` / `collector_agent.py` / `processor_agent.py` / `quality_agent.py` / `finance_agent.py` / `delivery_agent.py`）由我自己实现
3. Sol 只负责 S-1 到 S-5 的代码（boq_engine / risk_engine / supplier_risk / sources + 模板 / 聊天 UI）
4. S-5 聊天 UI 中的 `chat.py` **不需要**实现六 Agent 编排逻辑，只需调用现有的 `app/agents/coordinator.py` 接口

**S-5 chat.py 的职责边界**：
- ✅ 实现 `POST /chat/api` 和 `GET /chat/api/{session_id}` HTTP 接口
- ✅ 实现 session 管理（内存 dict）
- ✅ 实现 chat.html 渲染
- ❌ **不实现**六 Agent 编排逻辑（由我接入 coordinator.py）
- ✅ 调用 `app/agents/coordinator.py` 的 `run_pipeline(filters: dict) -> str` 接口（我会预留这个接口）

**我会预留的接口**（在 `app/agents/coordinator.py` 中）：
```python
async def run_pipeline(filters: dict) -> str:
    """运行六 Agent 协作 pipeline，返回 session_id。

    Args:
        filters: ParsedFilters 的 dict 形式

    Returns:
        session_id: 用于查询进度
    """
    # 异步启动六 Agent 协作
    # 立即返回 session_id
    # 前端通过 GET /chat/api/{session_id} 轮询进度
```

Sol 在 chat.py 中这样调用：
```python
from app.agents.coordinator import run_pipeline

@router.post("/api")
async def chat_api(req: ChatRequest):
    # 1. 意图解析（同步，立即返回）
    parsed_filters = await parse_query(req.query)
    # 2. 启动六 Agent pipeline（异步，立即返回 session_id）
    session_id = await run_pipeline(parsed_filters.dict())
    # 3. 返回第一阶段响应
    return ChatResponse(
        session_id=session_id,
        parsed_filters=parsed_filters.dict(),
        stage="intent",
        progress=10,
        ...
    )
```

**最终文件数**：Sol 的 S-1 到 S-5 仍按原计划 22 个文件交付，agents 层由我额外实现。

---

## 总结

| 问题 | 回答 |
|---|---|
| Q1 模板数量 | 7 个模板文件 + 1 个 sources.py = 8 个文件 |
| Q2 进度查询接口 | 允许，必须增加 `GET /chat/api/{session_id}` |
| Q3 agents 层 | 不强制 Sol 新增，由我实现；Sol 的 chat.py 调用我预留的 `run_pipeline()` 接口 |

收到 ZIP 和这 3 项确认后，请按 S-1 → S-5 处理。
