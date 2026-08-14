# 贡献指南

感谢您对标小智项目的关注！本文档说明如何参与贡献。

## 开发环境准备

```bash
git clone https://github.com/tlyyxjz/BidAgent.git
cd BidAgent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## 项目结构

```
BidAgent/
├── app/agents/        # 6 Agent 实现
├── app/api/           # FastAPI 路由
├── app/core/          # 核心组件（浏览器池、会话管理）
├── app/models/        # 数据库模型
├── app/processors/    # 算法处理器（SimHash、反幻觉、BOQ）
├── app/llm/           # LLM 抽取层（extractor/parser/prompts）
├── app/services/      # 业务服务（data_deletion/）
├── app/utils/         # 凭证与工具（credentials/api_key/aes_crypto/url_safety/logger）
├── app/report/        # Word 报告生成（docx_generator/docx_sections）
├── app/templates/     # 采集器模板
├── app/scheduler/     # 定时调度器
├── static/            # 前端页面
├── tests/             # 测试用例（2031 项，含 parametrize 展开，覆盖率 88.85%）
├── docs/              # 文档
└── scripts/           # 工具脚本
```

## 提交规范

### 分支策略
- `main`：稳定发布分支，仅通过 PR 合并
- `feature/*`：功能开发分支
- `fix/*`：Bug 修复分支
- **禁止直接推送到 main 分支**

### Commit 格式
```
<类型>: <简短描述>

feat: 新功能 | fix: Bug 修复 | refactor: 重构 | test: 测试 | docs: 文档
```

### 测试要求
```bash
python -m pytest tests/ -v
python -m pytest --cov=app --cov-report=term-missing  # 覆盖率 ≥90%
```

## 开源协议

Apache License 2.0

## 联系方式
- 邮箱：135****8907@163.com
- GitHub Issues：https://github.com/tlyyxjz/BidAgent/issues
