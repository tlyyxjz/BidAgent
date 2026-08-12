"""ScrapeFlow 应用配置，从环境变量加载。

工程规范：
- SECRET_KEY 必须为 64 字符 hex（用 `secrets.token_hex(32)` 生成）。
- 所有敏感字段均有校验器，缺失或弱密钥直接 sys.exit(1) 并打印清晰提示。
"""

from __future__ import annotations

import sys

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """定义应用配置。"""

    # 64 字符 hex 密钥（token_hex(32) 生成）
    SECRET_KEY: str
    # 管理员密钥（/admin 路由）
    ADMIN_SECRET: str
    # 数据库 URL（MVP: SQLite + aiosqlite）
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/scrapeflow.db"
    # Redis URL（队列 + 速率限制计数）
    REDIS_URL: str = "redis://localhost:6379/0"
    # 代理池（逗号分隔）
    PROXY_LIST: str = ""
    # Playwright
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_TIMEOUT_SECONDS: int = 30
    # 免费套餐每日限额
    FREE_TIER_DAILY_LIMIT: int = 5
    # Sentry
    SENTRY_DSN: str = ""
    # CORS（C-4 修复：默认仅允许本地开发，生产环境必须配置具体域名）
    CORS_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"
    # LLM 配置（意图解析）
    LLM_PROVIDER: str = "deepseek"  # deepseek / dashscope
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL: str = "deepseek-chat"
    LLM_TIMEOUT_SECONDS: int = 30
    # 多模型支持：抽取专用模型覆盖（空=回落 LLM_MODEL）
    LLM_EXTRACTION_MODEL: str = ""
    # 显式覆盖任意 OpenAI 兼容端点（优先级高于 provider 专属配置）
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    # json_object response_format 开关（空=按 provider 默认）
    LLM_JSON_MODE: str = ""
    # LLM 抽取上限（每批最多抽取多少条公告，避免全量抽取超时）
    LLM_EXTRACT_MAX: int = 10
    # 智谱 GLM / OpenAI（多 provider 可切换）
    ZHIPU_API_KEY: str = ""
    ZHIPU_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    # 报告输出目录（命题交付物）
    REPORT_OUTPUT_DIR: str = "data/reports"
    # 附件下载目录（命题第 4 项硬要求）
    ATTACHMENT_DIR: str = "data/attachments"
    # M-8 修复：cookie 目录独立配置（不再从 ATTACHMENT_DIR 推导）
    COOKIE_DIR: str = "data/cookies"
    # 应用角色（web/worker/scheduler，避免双容器重复抓取）
    APP_ROLE: str = "web"
    # Webhook 密钥（推送签名）
    WEBHOOK_SECRET: str = ""
    # 应用基础 URL（推送链接）
    APP_BASE_URL: str = "http://localhost:8000"

    # ==== SMTP 邮件推送（命题第 6 项硬要求）====
    # Sol S-10：真实邮件发送（STARTTLS / SMTP over SSL）
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    # True=STARTTLS（587），False=SMTP_SSL（465）；不能配置为不加密明文
    SMTP_USE_TLS: bool = True
    SMTP_FROM_ADDR: str = ""
    SMTP_FROM_NAME: str = "ScrapeFlow 招标推送"
    SMTP_TIMEOUT: int = 30

    # ==== 登录态与浏览器池（命题第 2 项硬要求：登录态采集）====
    # Sol S-11：Playwright storage_state 持久化
    ANTI_DETECT_ENABLED: bool = True
    ANTI_DETECT_HEADLESS: bool = True
    ANTI_DETECT_NO_SANDBOX: bool = False
    ANTI_DETECT_SESSION_DIR: str = "data/sessions"
    BROWSER_POOL_SIZE: int = 2
    BROWSER_POOL_TIMEOUT: int = 30

    # ==== 千里马登录（命题第 2 项硬要求：≥1 个登录态网站）====
    # QIANLIMA_PASSWORD 不应提交到 Git；推荐登录脚本运行时用 getpass 输入
    QIANLIMA_USERNAME: str = ""
    QIANLIMA_PASSWORD: str = ""
    QIANLIMA_LOGIN_URL: str = "https://vip.qianlima.com/login.html"

    # ==== 速率限制后端要求（BE-H9）====
    # False（默认）：Redis 不可用时 fallback 到内存计数器（适合开发环境）。
    # True：Redis 不可用时拒绝请求（fail-closed，生产环境推荐）。
    RATE_LIMIT_REQUIRE_REDIS: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        """校验 SECRET_KEY 必须是 32 字节的 hex 字符串。"""
        try:
            decoded = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError(
                "SECRET_KEY 必须是 64 字符 hex 字符串。"
                ' 用 python -c "import secrets; print(secrets.token_hex(32))" 生成'
            ) from exc

        if len(decoded) != 32:
            raise ValueError(
                f"SECRET_KEY 必须编码 32 字节（当前 {len(decoded)} 字节）。"
                " 用 python -c \"import secrets; print(secrets.token_hex(32))\" 生成"
            )
        return value.lower()

    @field_validator("ADMIN_SECRET")
    @classmethod
    def validate_admin_secret(cls, value: str) -> str:
        """校验管理员密钥非空且足够长。"""
        if not value.strip():
            raise ValueError("ADMIN_SECRET 不能为空")
        if len(value) < 8:
            raise ValueError("ADMIN_SECRET 至少 8 字符")
        return value

    @field_validator("FREE_TIER_DAILY_LIMIT")
    @classmethod
    def validate_free_limit(cls, value: int) -> int:
        """校验免费限额为正整数。"""
        if value < 1:
            raise ValueError("FREE_TIER_DAILY_LIMIT 必须 >= 1")
        return value

    # Sol S-10/S-11：SMTP 端口/超时和浏览器池大小必须为正
    @field_validator(
        "SMTP_PORT",
        "SMTP_TIMEOUT",
        "BROWSER_POOL_SIZE",
        "BROWSER_POOL_TIMEOUT",
    )
    @classmethod
    def validate_positive_config(cls, value: int) -> int:
        """校验端口、超时和浏览器池大小必须 > 0。"""
        if value <= 0:
            raise ValueError("端口、超时和浏览器池大小必须 > 0")
        return value

    @property
    def proxies(self) -> list[str]:
        """返回代理列表（已去除空白项）。"""
        if not self.PROXY_LIST.strip():
            return []
        return [p.strip() for p in self.PROXY_LIST.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        """返回 CORS 允许的源列表。"""
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


def _load_settings_or_exit() -> Settings:
    """加载配置；缺少必需变量时打印清晰提示并退出。"""
    try:
        return Settings()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[CONFIG ERROR] 配置加载失败:\n  {exc}\n", file=sys.stderr)
        print(
            "请检查 .env 文件或环境变量。必需变量:\n"
            "  SECRET_KEY      64字符hex (用 python -c \"import secrets; print(secrets.token_hex(32))\" 生成)\n"
            "  ADMIN_SECRET    至少8字符的管理密钥\n",
            file=sys.stderr,
        )
        sys.exit(1)


settings = _load_settings_or_exit()
