# GPT-5.6 Sol 专属任务清单（精简版 v3）

**更新时间**：2026-07-19
**说明**：GLM-5.2 已经把所有能做的都做完了，Sol 只剩 3 项真正需要调参/实战经验的难点。

---

## 本轮 GLM-5.2 已完成的任务（无需 Sol 再做）

| 原任务 | 状态 | 完成方式 |
|---|---|---|
| S-3 scraper.py cookies 传递 | ✅ GLM 完成 | 修改 `_merge_template` 读取 `template.cookies` + 用户字段覆盖 |
| S-4 拆分 ui.py（490 行 → 41 行） | ✅ GLM 完成 | HTML 拆到 `app/templates/html/{index,subscriptions,tenders}.py` |
| S-5 反幻觉正则误报 | ✅ GLM 完成 | 招标编号正则收紧：必须含数字+连字符，或匹配 SH-/ZB-/GG- 等前缀 |
| S-6 cron 默认 base 时间 | ✅ GLM 完成 | `last_run=None` 时返回 False；调用方传 `last_run or sub.created_at` |
| S-7 拆分 auth.py（298 行 → 115 行） | ✅ GLM 完成 | admin 路由拆到 `app/api/admin.py`，main.py 改 import 路径 |
| S-8 tender.py 重复 session | ✅ GLM 完成 | 3 个公共查询路由改为 `Depends(get_db)` |

**测试结果**：90 passed, 30 warnings in 275.35s（0:04:35）

---

## Sol 要做的事（共 3 项，预估 $0.9 美元）

### 🔥 S-1: 千里马验证码识别（~80 行，真难点）

**为什么给 Sol**：ddddocr 模型调参 + Playwright 模拟登录 + cookie 持久化 + 401 重试，GLM-5.2 鲁棒性差

**需求**：新建 `app/templates/qianlima_login.py`
```python
async def login_and_save_cookies(
    username: str,
    password: str,
    login_url: str = "https://www.qianlima.com/login",
    cookie_file: Path = data/cookies/qianlima.json,
) -> dict:
    """
    1. Playwright 打开登录页
    2. 截图验证码 → ddddocr 识别
    3. 填用户名/密码/验证码 → 提交
    4. 检测登录成功（URL 跳转或元素出现）
    5. 持久化 cookie 到 JSON 文件
    6. 返回 {"success": bool, "cookies": [...], "error": str|None}
    """
```

**约束**：失败重试 ≤ 3 次，cookie 路径用 settings 推导，401 自动重登，单文件 ≤ 300 行

**预期 token**：~$0.5

---

### 🔥 S-2: 升级 SimHash 词频加权（~30 行，短难点）

**为什么给 Sol**：当前等权实现准确率约 75%，升级 TF-IDF 加权到 90%+ 需要调参

**需求**：修改 `app/processors/simhash.py` 的 `compute_simhash`
```python
from collections import Counter
import math

token_freq = Counter(tokens)
for token, freq in token_freq.items():
    tf = 1 + math.log(freq)  # TF 权重（log 归一化）
    h = _hash64(token)
    for i in range(64):
        if h & (1 << i):
            weights[i] += tf
        else:
            weights[i] -= tf
```

**约束**：不破坏现有 90 测试，汉明距离阈值保持 3，jieba 退化方案保留

**预期 token**：~$0.2

---

### 🔥 S-9: queue.py Redis 断线重连（~30 行，健壮性）

**为什么给 Sol**：需要实战经验调连接池参数 + 指数退避策略

**问题**：`app/core/queue.py` 的 `_get_redis_connection` 每次调用都新建 Redis 客户端，没有连接池复用；Redis 断线时直接降级到线程池，没有重试

**需求**：
1. 全局 Redis 客户端单例 + 连接池
2. Redis 断线后最多重试 3 次（指数退避 1s/2s/4s）
3. 重试失败才降级到线程池
4. Redis 恢复后自动恢复使用

```python
_redis_client: Redis | None = None
_redis_failed_at: float | None = None

def _get_redis_connection() -> Redis | None:
    global _redis_client, _redis_failed_at
    if _redis_client and _redis_client.ping():
        return _redis_client
    # 重试逻辑...
```

**约束**：测试环境无 Redis 时不阻塞

**预期 token**：~$0.2

---

## 验证标准

1. `python -m pytest --tb=short` 必须 90 测试全过（当前基线）
2. 千里马登录流程能跑通（mock 环境也行）
3. SimHash 升级后准确率提升（不破坏现有测试）
4. Redis 断线重连不影响测试环境

## 预期 Sol 总消耗

| 任务 | 成本 |
|---|---|
| S-1 千里马验证码 | $0.5 |
| S-2 SimHash 升级 | $0.2 |
| S-9 queue.py Redis 重连 | $0.2 |
| 调试 bug 2 次 | $0.5 |
| **总计** | **$1.4 ≈ ¥10 RMB** |

**建议充值 ¥15 RMB（$2 美元）就够 Sol 用了。**

---

## 附：GLM-5.2 已识别但未修的代码薄弱点（可选优化，不阻塞答辩）

### 1. SimHash 候选集合扫描问题
`app/processors/tender_ingestor.py` 的 `_find_duplicate` 取最近 1000 条做汉明距离比对，数据量大时会漏掉旧记录的重复

**Sol 建议**：升级到 PostgreSQL 时用 LSH（Locality-Sensitive Hashing）索引，或按 source_platform 分组扫描

### 2. 联系人字段长度不一致
`app/models/tender.py` 的 `contact_phone` / `contact_email` 字段长度 64（SHA256 hex 是 64 字符），但 `app/api/tender.py` 的请求模型也限制 64 字符，用户传明文会被截断

**Sol 建议**：API 层接受明文 + 长度 20，service 层 SHA256 后入库

### 3. LLM 缓存只增不删
`app/llm/parser.py` 的 `_semantic_cache` 虽然加了 TTL + LRU 淘汰，但进程重启后缓存丢失，没有持久化

**Sol 建议**：MVP 可接受，生产环境用 Redis 替代 dict

### 4. 反幻觉校验无 LLM 兜底
`app/processors/hallucination_checker.py` 只用正则提取事实，无法识别语义幻觉（如"该项目位于北京"实际原文是"位于上海"）

**Sol 建议**：可选接入 LLM 做语义一致性校验（增加成本）

### 5. 附件下载无并发控制
`app/processors/attachment_downloader.py` 单次下载，没有并发限制

**Sol 建议**：用 `asyncio.Semaphore(5)` 限制并发数

---

**文档结束**
