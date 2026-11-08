"""ccgp 采集加固层（S19：退避重试 + 熔断冷却 + 断点续采 + 分层降级编排）。

四件套：
1. 指数退避重试 —— 可重试错误（超时/网络抖动）按指数退避 + 抖动重试，
   有次数与延迟上限，避免锤站；
2. 熔断冷却 —— 403/被拦或连续失败进入平台级冷却窗口，指数增长
   （60s 起步 → 30min 封顶），冷却期内零请求（不锤墙）；
3. 断点续采 —— 熔断器与最近成功位点落盘 data/ccgp_checkpoint.json
   （原子写），进程重启不丢状态；
4. 分层降级编排 —— ccgp 主源失败 → 省级实时源（DEFAULT_SOURCES）
   并发兜底采集，payload 交调用方串行去重入库。

合规纪律（与 realtime_sources.py 同纲，硬编码不是注释承诺）：
- 403/被拦零重试（重试只会加重封禁）；
- robots.txt 拒绝是致命错误（不重试、不计入熔断——是政策不是故障）；
- 降级源采集走 BaseSourceAdapter 合规包装（robots 先行/限流/403 即停）。

纯函数层：无网络、无数据库，全部可离线测试。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

logger = get_logger("scheduler.ccgp_resilience")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHECKPOINT_PATH = _PROJECT_ROOT / "data" / "ccgp_checkpoint.json"


# ---------------------------------------------------------------- 退避重试

@dataclass(frozen=True)
class RetryPolicy:
    """退避重试策略（默认值针对 ccgp 搜索口调校）。"""

    max_attempts: int = 3      # 总尝试次数（含首次）
    base_delay: float = 2.0    # 首次重试前等待秒
    factor: float = 2.0        # 指数底数
    max_delay: float = 30.0    # 单次等待上限
    jitter: float = 0.25       # 乘性抖动幅度 ±25%


def backoff_delays(policy: RetryPolicy) -> list[float]:
    """生成尝试之间的等待序列：base, base*f, ...，封顶 max_delay。"""
    delays: list[float] = []
    d = policy.base_delay
    for _ in range(max(0, policy.max_attempts - 1)):
        delays.append(min(d, policy.max_delay))
        d *= policy.factor
    return delays


def add_jitter(delay: float, jitter: float) -> float:
    """乘性抖动 ±jitter，避免多订阅同时触发造成重试风暴同步。"""
    if jitter <= 0:
        return delay
    return max(0.0, delay * (1.0 + random.uniform(-jitter, jitter)))


_BLOCKED_MARKS = ("403", "forbidden", "blocked", "waf", "拦截")
_FATAL_MARKS = ("robots.txt", "url 不安全", "url not safe", "unsupported")


def classify_failure(error_text: str) -> str:
    """把错误文本归类为 blocked / fatal / retryable。

    - blocked：反爬拦截，立即熔断，零重试；
    - fatal：政策/配置问题（robots 拒绝、SSRF、不支持的平台），重试无意义，
      且不计入熔断；
    - retryable：超时/网络抖动等瞬时错误，退避重试。
    fatal 先判（robots 拒绝消息里可能同时含 forbidden 字样）。
    """
    t = (error_text or "").lower()
    if any(m in t for m in _FATAL_MARKS):
        return "fatal"
    if any(m in t for m in _BLOCKED_MARKS):
        return "blocked"
    return "retryable"


# ---------------------------------------------------------------- 熔断+续采

class SourceBreaker:
    """平台级熔断器 + 断点持久化。

    连续失败达到 failure_threshold 后进入冷却：
    cooldown = min(base * 2^(failures - threshold), max_cooldown)，
    随失败次数指数增长并封顶；record_success 清零恢复。
    checkpoint_path 给定时每次状态变更原子落盘（断点续采）。
    """

    def __init__(
        self,
        failure_threshold: int = 2,
        base_cooldown: float = 60.0,
        max_cooldown: float = 1800.0,
        checkpoint_path: Path | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self.checkpoint_path = checkpoint_path
        self._platforms: dict[str, dict[str, Any]] = {}
        if checkpoint_path is not None:
            self._load(checkpoint_path)

    # ---- 状态机 ----

    def record_success(self, platform: str) -> None:
        st = self._platforms.get(platform, {})
        st.update({
            "consecutive_failures": 0,
            "cooldown_until": 0.0,
            "last_success": _now_iso(),
            "last_error": None,
        })
        self._platforms[platform] = st
        self._persist()

    def record_failure(self, platform: str, error: str | None = None) -> None:
        st = self._platforms.get(platform, {})
        failures = int(st.get("consecutive_failures", 0)) + 1
        cooldown_until = 0.0
        if failures >= self.failure_threshold:
            span = self.base_cooldown * (2 ** (failures - self.failure_threshold))
            cooldown_until = time.time() + min(span, self.max_cooldown)
        st.update({
            "consecutive_failures": failures,
            "cooldown_until": cooldown_until,
            "last_error": error,
        })
        self._platforms[platform] = st
        self._persist()

    def cooldown_seconds(self, platform: str, now: float | None = None) -> float:
        """该平台剩余冷却秒数；0 表示可请求。"""
        st = self._platforms.get(platform)
        if not st:
            return 0.0
        until = float(st.get("cooldown_until", 0.0))
        now = now if now is not None else time.time()
        return max(0.0, until - now)

    def snapshot(self) -> dict[str, Any]:
        """深拷贝当前状态（供摘要/诊断使用）。"""
        return json.loads(json.dumps(self._platforms))

    # ---- 断点持久化 ----

    def _persist(self) -> None:
        if self.checkpoint_path is None:
            return
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "platforms": self._platforms,
        }
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.checkpoint_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.checkpoint_path)
        except OSError as exc:
            logger.warning("checkpoint save failed: %s", exc)

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            platforms = data.get("platforms")
            if isinstance(platforms, dict):
                self._platforms = {
                    k: v for k, v in platforms.items() if isinstance(v, dict)
                }
        except (OSError, ValueError) as exc:
            logger.warning("checkpoint load failed (fresh start): %s", exc)
            self._platforms = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


_BREAKER: SourceBreaker | None = None


def get_breaker() -> SourceBreaker:
    """进程级单例（默认断点文件 data/ccgp_checkpoint.json）。"""
    global _BREAKER
    if _BREAKER is None:
        _BREAKER = SourceBreaker(checkpoint_path=DEFAULT_CHECKPOINT_PATH)
    return _BREAKER


# ---------------------------------------------------------------- 降级编排

async def run_fallback_collect(limit: int = 3) -> list[dict[str, Any]]:
    """分层降级：ccgp 主源失败 → 省级实时源并发兜底。

    复用 SourceAdapter 合规包装（robots 先行/域内限流/403 即停），
    单源失败不影响其他源。返回 payload 列表，由调用方串行去重入库
    （与 realtime_panel 同一入库纪律）。
    """
    from app.core.robots_checker import robots_checker
    from app.services.realtime_sources import collect_realtime, resolve_adapters

    adapters = resolve_adapters()
    outcome = await collect_realtime(
        adapters, limit=limit, robots_checker=robots_checker
    )
    payloads: list[dict[str, Any]] = []
    for r in outcome["results"]:
        if r.ok:
            payloads.extend(r.payloads)
        else:
            logger.warning(
                "fallback source failed source=%s status=%s err=%s",
                r.source, r.status, r.error,
            )
    logger.info(
        "fallback collect done ok=%s payloads=%d",
        outcome["ok_count"], len(payloads),
    )
    return payloads
