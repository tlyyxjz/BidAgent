"""BidAgent 阶梯压测 —— locust 配置

用法（三条命令复现）：
    pip install locust
    # 无 UI 模式，阶梯 1→10→50→100，每档 3 分钟
    locust -f _perf/locustfile.py --headless -u 100 -r 10 -t 12m \
           --host http://127.0.0.1:8011 --csv _perf/out/run_$(date +%Y%m%d_%H%M)

⚠️ 前置：
  1. 确认服务是干净的（重启一次，避免带着昨天的连接池状态）
  2. 确认限流已关（见 _perf/README.md），否则压出来的是 429，不是真实曲线
  3. 只压只读接口 —— 写接口会调 LLM 并写 SQLite，既烧钱又污染基线
"""

from locust import HttpUser, task, between, events
import os

# 期望的响应体下限（字节）。低于这个数说明返回的是错误页/空数据，必须让测试失败而不是记成"快"。
MIN_BODY = int(os.getenv("BIDAGENT_MIN_BODY", "150000"))
# 取一个真实存在的 tender id（829 条，1 一定在）
TENDER_ID = os.getenv("BIDAGENT_TENDER_ID", "1")
# T2 预热：正式统计前先打的丢弃请求数（0 = 关掉）
WARMUP = int(os.getenv("BIDAGENT_WARMUP", "5"))


class ReadUser(HttpUser):
    """模拟真实读流量：列表页为主，详情页为辅（列表:详情 ≈ 4:1）。"""

    wait_time = between(0.1, 0.5)

    @task(4)
    def list_tenders(self):
        # 这是当前最重的接口：829 条全量 + 360 行字段拼装（三列；全表 582 行），无分页，单次 ~187KB
        with self.client.get("/api/real/tenders", name="/api/real/tenders",
                             catch_response=True, timeout=60) as r:
            if r.status_code != 200:
                r.failure(f"status={r.status_code}")
            elif len(r.content) < MIN_BODY:
                r.failure(f"body too small: {len(r.content)} < {MIN_BODY}")

    @task(1)
    def tender_detail(self):
        with self.client.get(f"/api/real/tenders/{TENDER_ID}/detail",
                             name="/api/real/tenders/[id]/detail",
                             catch_response=True, timeout=60) as r:
            if r.status_code != 200:
                r.failure(f"status={r.status_code}")
            elif len(r.content) < 1000:
                r.failure(f"body too small: {len(r.content)}")


@events.test_start.add_listener
def _on_start(environment, **_kwargs):
    print(f"[bidagent-perf] host={environment.host} min_body={MIN_BODY}")
    _warmup(environment.host, WARMUP)


# ---------------------------------------------------------------------------
# T2 预热隔离：冷启动 111ms vs 稳态 37ms（3×），不预热会让每档的 P99 被第一个样本定义。
# 这里把它写进 locust，而不是靠人记得先 curl 五次 —— 人一定会忘。
# 说明：test_start 在虚拟用户启动之前触发，所以这几个请求不进统计。
# ---------------------------------------------------------------------------
def _warmup(host: str, n: int) -> None:
    import requests

    url = host.rstrip("/") + "/api/real/tenders"
    ok = 0
    for i in range(n):
        try:
            r = requests.get(url, timeout=60)
            ok += 1 if r.status_code == 200 else 0
            print(f"[warmup] {i + 1}/{n} status={r.status_code} {len(r.content) / 1024:.1f}KB")
        except Exception as e:  # noqa: BLE001
            print(f"[warmup] {i + 1}/{n} failed: {e}")
    print(f"[warmup] 完成 {ok}/{n}，冷启动开销已丢弃，统计从稳态开始")

