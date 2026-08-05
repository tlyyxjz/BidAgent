# =====================================================================

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install -r requirements.txt


FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/appuser/.local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
       libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
       libxdamage1 libxfixes3 libxrandr2 libgbm1 \
       libasound2 libatspi2.0-0 libwayland-client0 \
       libgtk-3-0 libpango-1.0-0 libcairo2 libdbus-1-3 \
       libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system appuser \
    && useradd --system --gid appuser \
       --create-home --home-dir /home/appuser appuser

WORKDIR /app

# 从 builder 复制已安装的依赖
COPY --from=builder /install /usr/local

# 复制应用代码
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY qianlima-dom.json* ./qianlima-dom.json

# 创建数据目录并设置权限（data/ 包含 sessions/cookies/reports/attachments）
RUN mkdir -p /app/data/sessions /app/data/cookies /app/data/reports /app/data/attachments \
    && chown -R appuser:appuser /app

# 安装 Playwright Chromium 浏览器（非 root 用户）
USER appuser
RUN python -m playwright install chromium

# 修复 Playwright 缓存目录权限（playwright install 可能写入 root 拥有的缓存）
USER root
RUN chown -R appuser:appuser /home/appuser/.cache/ms-playwright 2>/dev/null || true \
    && chown -R appuser:appuser /ms-playwright 2>/dev/null || true

USER appuser

EXPOSE 8000

# 健康检查（curl /health）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
