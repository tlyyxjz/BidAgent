# BidAgent 部署指南

本文介绍如何用 Docker 部署 BidAgent API，以及如何部署到 Fly.io / Railway。

## 1. Docker 部署（推荐）

### 1.1 准备环境变量

```bash
cp .env.example .env
```

生成必需的密钥：

```bash
# SECRET_KEY: 64 字符 hex
python -c "import secrets; print(secrets.token_hex(32))"

# ADMIN_SECRET: 至少 8 字符
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

编辑 `.env`：

```env
SECRET_KEY=<上面生成的 64 字符 hex>
ADMIN_SECRET=<上面生成的 admin 密钥>
DATABASE_URL=sqlite+aiosqlite:///./data/bidagent.db
REDIS_URL=redis://redis:6379/0
PROXY_LIST=                          # Pro 套餐才需要
FREE_TIER_DAILY_LIMIT=5
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT_SECONDS=30
SENTRY_DSN=                          # 可选
CORS_ORIGINS=*
```

### 1.2 启动服务

```bash
docker compose up -d --build
```

`docker-compose.yml` 会启动 3 个容器：

| 服务 | 说明 |
|---|---|
| `biaoxiaozhi` | FastAPI 应用，端口 8000 |
| `redis` | Redis 7，队列 + 速率限制计数 |
| `worker` | RQ worker，执行异步抓取任务 |

查看状态：

```bash
docker compose ps
docker compose logs -f biaoxiaozhi
```

### 1.3 验证部署

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/
# {"name":"标小智 API","version":"4.1","docs":"/docs","health":"/health"}
```

### 1.4 创建用户并开始使用

```bash
# 创建用户
curl -X POST http://localhost:8000/admin/users \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","plan":"free"}'

# 生成 API key（假设 user id = 1）
curl -X POST http://localhost:8000/admin/users/1/api-keys \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"name":"production"}'

# 用返回的 API key 抓取
curl -X POST http://localhost:8000/api/scrape \
  -H "Authorization: Bearer sk_xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","selectors":{"title":"h1"}}'
```

## 2. Fly.io 部署

### 2.1 安装 flyctl

```bash
# macOS
brew install flyctl

# Linux
curl -L https://fly.io/install.sh | sh

# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex
```

### 2.2 创建应用

```bash
cd BidAgent
fly launch --no-deploy
```

回答交互问题：
- App name: `biaoxiaozhi-api`（或自定义）
- Select region: 选择离用户最近的
- Would you like to set up a Postgresql database? **Yes**（生产用 PostgreSQL）
- Would you like to set up a Redis database? **Yes**（用 Upstash Redis）

`fly launch` 会自动生成 `fly.toml`。

### 2.3 配置 fly.toml

修改 `fly.toml` 添加 Playwright 依赖与 secrets：

```toml
[app]
primary_region = "nrt"  # 改成你的区域

[build]
  dockerfile = "Dockerfile"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0

[[http_service.checks]]
  interval = "30s"
  timeout = "5s"
  grace_period = "10s"
  method = "GET"
  path = "/health"

[vm]
  memory = "1gb"  # Playwright 需要 ≥ 1GB
  cpu_kind = "shared"
  cpus = 1
```

### 2.4 设置 secrets

```bash
fly secrets set SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
fly secrets set ADMIN_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
fly secrets set DATABASE_URL="postgresql+asyncpg://user:pass@your-pg-host:5432/biaoxiaozhi"
fly secrets set REDIS_URL="rediss://default:password@your-upstash-host:6379"
fly secrets set FREE_TIER_DAILY_LIMIT=5
fly secrets set PLAYWRIGHT_HEADLESS=true
fly secrets set SENTRY_DSN=""
```

注意：生产环境用 PostgreSQL 时把 `DATABASE_URL` 改成 `postgresql+asyncpg://...`，并在 `requirements.txt` 加 `asyncpg`。

### 2.5 部署

```bash
fly deploy
```

部署完成后打开：

```bash
fly apps open
```

### 2.6 部署 RQ Worker（可选）

Fly.io 上推荐把 worker 作为单独的 app 部署：

```bash
# 创建 worker app
fly apps create biaoxiaozhi-worker

# 在 worker app 中设置同样的 secrets
fly secrets set --app biaoxiaozhi-worker SECRET_KEY=... REDIS_URL=...

# 部署 worker（用同样的 Dockerfile，覆盖 command）
fly deploy --app biaoxiaozhi-worker \
  --strategy rolling
```

在 `fly.toml` 中为 worker 配置：

```toml
[app]
name = "biaoxiaozhi-worker"

[build]
  dockerfile = "Dockerfile"

[deploy]
  release_command = "python -m app.worker_loop"

[vm]
  memory = "1gb"
```

## 3. Railway 部署

### 3.1 创建项目

1. 登录 https://railway.app
2. New Project → Deploy from GitHub repo
3. 选择 BidAgent 仓库

### 3.2 添加服务

- **Service 1**: Web 服务（FastAPI）
  - Source: 仓库根目录
  - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Service 2**: Worker 服务（RQ）
  - Source: 仓库根目录
  - Start command: `python -m app.worker_loop`
- **Database 1**: Redis（Railway 提供）
- **Database 2**: PostgreSQL（Railway 提供，可选，生产推荐）

### 3.3 配置环境变量

在 Web 和 Worker 服务的 Variables 标签中添加：

| 变量 | 值 |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` 生成 |
| `ADMIN_SECRET` | 自定义 ≥ 8 字符 |
| `DATABASE_URL` | Railway 提供的 PostgreSQL 连接串（改成 `postgresql+asyncpg://...`） |
| `REDIS_URL` | Railway 提供的 Redis 连接串 |
| `PLAYWRIGHT_HEADLESS` | `true` |
| `FREE_TIER_DAILY_LIMIT` | `5` |

### 3.4 部署

Railway 会自动构建并部署。部署完成后在 Settings → Networking 生成公网域名。

## 4. 生产环境检查清单

部署到生产前确认：

- [ ] `SECRET_KEY` 用 `secrets.token_hex(32)` 生成（不是手填）
- [ ] `ADMIN_SECRET` 至少 12 字符
- [ ] `.env` 文件不在 Git 仓库（已在 `.gitignore`）
- [ ] DATABASE_URL 指向 PostgreSQL（不是 SQLite）
- [ ] REDIS_URL 指向独立 Redis 实例
- [ ] 至少部署 1 个 RQ worker 容器
- [ ] `PROXY_LIST` 已配置（Pro 套餐用户）
- [ ] `SENTRY_DSN` 已配置（生产监控）
- [ ] `CORS_ORIGINS` 已限制为前端域名（不是 `*`）
- [ ] 健康检查 `/health` 可访问
- [ ] 已通过管理后台创建至少 1 个用户 + API key

## 5. 升级 PostgreSQL（从 SQLite 迁移）

MVP 用 SQLite，生产建议迁移到 PostgreSQL：

1. 在 `requirements.txt` 添加 `asyncpg`
2. 修改 `.env`：
   ```env
   DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/biaoxiaozhi
   ```
3. 重启服务，`lifespan` 钩子会自动 `create_all` 建表
4. （可选）用 Alembic 管理后续 migration

## 6. 监控与日志

### 6.1 日志查看

```bash
# Docker
docker compose logs -f biaoxiaozhi
docker compose logs -f worker

# Fly.io
fly logs

# Railway
# 在 Dashboard → Deployments → Logs
```

日志格式（带 request_id）：

```
2026-07-18 10:00:00.123 [INFO] [a1b2c3d4e5f6] app.main: starting BidAgent API
2026-07-18 10:00:01.456 [INFO] [a1b2c3d4e5f6] app.core.queue: job completed job_id=xxx
```

### 6.2 Sentry

设置 `SENTRY_DSN` 后，应用启动时自动初始化 Sentry SDK，捕获未处理异常。

### 6.3 健康检查

- `/health` - 应用层健康检查（Docker healthcheck 用）
- `/docs` - Swagger UI，可手动测试 API

## 7. 常见问题

### 7.1 Playwright 启动失败

错误：`Executable doesn't exist at /ms-playwright/...`

解决：在容器构建时安装 Chromium：

```bash
docker compose build --no-cache
```

`Dockerfile` 中已包含 `python -m playwright install chromium` 和所有系统依赖。

### 7.2 Redis 连接失败

应用启动时如果 Redis 不可用，会自动 fallback：
- 速率限制：内存计数器
- 任务队列：线程池同步执行

但生产环境强烈建议部署独立 Redis。

### 7.3 SQLite 锁错误

高并发下 SQLite 可能出现 `database is locked`。解决：
- 迁移到 PostgreSQL（生产推荐）
- 或降低并发（`uvicorn --workers 1`）

### 7.4 速率限制不生效

检查：
1. Redis 是否可连接（`redis-cli ping`）
2. 用户 `plan` 字段是否正确（`free` / `starter` / `pro`）
3. 是否传了正确的 `Authorization: Bearer ...` 头

### 7.5 抓取超时

默认超时 30 秒。可在 `.env` 调整：

```env
PLAYWRIGHT_TIMEOUT_SECONDS=60
```

或针对单次请求在请求体中传 `max_pages` 降低翻页数。

## 8. 备份与恢复

### SQLite 备份

```bash
docker compose stop biaoxiaozhi worker
cp data/bidagent.db data/bidagent.db.backup.$(date +%Y%m%d)
docker compose start biaoxiaozhi worker
```

### PostgreSQL 备份

```bash
pg_dump $DATABASE_URL > backup.sql
# 恢复
psql $DATABASE_URL < backup.sql
```

## 9. 卸载

```bash
docker compose down -v       # 停止并删除容器 + 数据卷
rm -rf data/                 # 删除本地数据
```

## 10. 私有化部署（客户自部署模型）

企业客户可自部署开源模型（千问 27B/32B 等，Ollama/vLLM），公告与尽调数据不出域；
我们交付证据引擎+验证规则+采集器。**换模型只改 .env，不改代码，不需 API Key。**

架构承诺：抽取走"LLM 提议、规则裁判"——模型只出候选值，证据定位不上原文即拒收。
因此换任何更弱的模型，顶多覆盖略降（拒收变多），**精确率不破**。

### 10.1 启动本地模型（Ollama 示例）

```bash
# 安装 Ollama 后拉取模型（32B 量化版约 20G 显存；7B 约 5G）
ollama pull qwen2.5:32b
ollama serve   # 默认监听 http://localhost:11434
```

### 10.2 配置 .env

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:32b
# OLLAMA_BASE_URL=http://localhost:11434/v1   # 默认值，非本机改这里
# LLM_JSON_MODE=false                          # 量化小模型不稳支持 json_object 时关闭
```

Docker 部署时容器访问宿主机 Ollama：`OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`
（compose 默认值已如此配置）。vLLM 同理：`LLM_PROVIDER=vllm` + `VLLM_BASE_URL`。

### 10.3 模型上岗质检（换模型前必跑）

```bash
# 小样本快速质检（默认 20 篇金标，输出本地化率/字段 F1/证据命中率 + 上岗结论）
python scripts/eval_model_readiness.py --limit 20
# 全量 598 篇质检（约 30-60 分钟，视硬件）
python scripts/eval_model_readiness.py
```

质检报告输出到 `_w3_outputs/model_readiness_report.json`。
上岗门槛（默认）：字段 F1 ≥ 0.80 且证据命中率 ≥ 0.95；不达标会明确给出 FAIL 结论。

### 10.4 验证本地链路

```bash
curl http://localhost:11434/v1/models   # 确认模型已加载
python -c "from app.llm.provider import resolve_provider; p=resolve_provider(); print(p.name, p.model, p.base_url)"
# 期望输出: ollama qwen2.5:32b http://localhost:11434/v1
```
