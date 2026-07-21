# 豆包代码审查 · 第八轮

## 背景

这是 ScrapeFlow 项目（超聚变命题 · 招投标信息聚合工具）的**第八轮审查**。

- **第七轮**：你给出 0 Critical + 0 Major + 3 Minor，评分 9.2/8.5/8.4
- **第八轮（本轮）**：GLM-5.2 清理了第七轮 3 个 Minor，并补了 53 个单元测试

请基于本轮**新增/修改的代码**做第八轮审查，重点关注：
1. 第七轮 3 个 Minor 是否彻底清理
2. 新增 53 个测试是否真正验证了关键场景
3. HostnameLRUCache 抽类后是否引入新问题
4. 测试覆盖度提升后，企业级成熟度评分能否再提升

命题硬要求（6+1 项）：
1. LLM 意图解析 5 槽位 / 2. ≥2 网站 + ≥1 登录态 / 3. SimHash 去重 / 4. 5 字段汇总+Word 命名 / 5. cron 定时 / 6. 增量推送 / 7. 反幻觉

---

## 本轮修改清单（共 4 项）

### 修改 1：m-1 修复 — 测试名诚实命名 + 真正测完整链路

**问题回顾**：第七轮报告称"test_is_safe_url_rejects_loopback_url 测试名误导，实际只测底层 _is_ip_blocked"。

**修复方案**：
1. 把误导测试改名为 `test_is_ip_blocked_rejects_loopback`（诚实命名）
2. 新增 3 个真正测 is_safe_url 完整链路的测试，用 `importlib.reload` 绕过 conftest mock：

```python
def test_is_safe_url_full_chain_rejects_loopback(self, monkeypatch):
    """完整链路：is_safe_url("http://127.0.0.1/") 应返回不安全。"""
    import importlib
    import app.utils.url_safety as url_safety_mod

    mocked_fn = url_safety_mod.is_safe_url
    real_mod = importlib.reload(url_safety_mod)  # 重新加载拿真实函数
    try:
        safe, reason = real_mod.is_safe_url("http://127.0.0.1/admin")
        assert not safe
        assert "loopback" in reason
    finally:
        importlib.reload(url_safety_mod)
        url_safety_mod.is_safe_url = mocked_fn  # 恢复 mock

def test_is_safe_url_rejects_ftp_scheme(self, monkeypatch):
    """is_safe_url 拒绝 ftp:// scheme。"""
    ...
    safe, reason = real_mod.is_safe_url("ftp://example.com/file")
    assert not safe
    assert "scheme" in reason

def test_is_safe_url_rejects_blacklisted_hostname(self, monkeypatch):
    """is_safe_url 拒绝黑名单域名 localhost。"""
    ...
    safe, reason = real_mod.is_safe_url("http://localhost/admin")
    assert not safe
    assert "internal hostname" in reason
```

**审查重点**：
- `importlib.reload` + try/finally 恢复 mock 的方式是否可靠？
- 是否会在并行测试时污染其他测试？
- file:// scheme 测试为什么改成了 ftp://？（file:/// 无 hostname 先撞 "invalid hostname"）

---

### 修改 2：m-2 修复 — 抽 HostnameLRUCache 成独立类

**问题回顾**：第七轮报告称"hostname LRU 缓存淘汰逻辑仍 0 测试，闭包实现不好测"。

**修复方案**：新建 `app/utils/hostname_cache.py`，把闭包抽成独立类：

```python
class HostnameLRUCache:
    """hostname 安全校验结果 LRU 缓存。

    缓存值结构：(is_safe: bool, reason: str)
    """

    def __init__(self, capacity: int = 64) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity 必须 > 0，实际 {capacity}")
        self._capacity = capacity
        self._data: OrderedDict[str, tuple[bool, str]] = OrderedDict()

    @property
    def capacity(self) -> int: return self._capacity

    @property
    def size(self) -> int: return len(self._data)

    def get(self, hostname: str) -> tuple[bool, str] | None:
        """查询缓存。命中时移动到末尾（LRU 策略）。"""
        if hostname not in self._data:
            return None
        value = self._data.pop(hostname)
        self._data[hostname] = value  # move-to-end
        return value

    def set(self, hostname: str, value: tuple[bool, str]) -> None:
        """写入缓存。超容量时淘汰最旧条目。"""
        if hostname in self._data:
            self._data.pop(hostname)
        self._data[hostname] = value
        while len(self._data) > self._capacity:
            self._data.popitem(last=False)

    def clear(self) -> None: self._data.clear()
    def __contains__(self, hostname: str) -> bool: return hostname in self._data
    def __len__(self) -> int: return len(self._data)
```

**scraper.py 改用新类**：
```python
from app.utils.hostname_cache import HostnameLRUCache

hostname_cache = HostnameLRUCache(capacity=64)

async def _ssrf_guard(route):
    ...
    if hostname_lower:
        cached = hostname_cache.get(hostname_lower)
        if cached is not None:
            ...
        safe, reason = await is_safe_url_async(req_url)
        hostname_cache.set(hostname_lower, (safe, reason))
```

**审查重点**：
- 类抽出来后，可测试性是否真的提升？
- LRU move-to-end 语义（pop + 重新插入）是否正确？
- capacity=0 抛 ValueError 是否合理？
- `__contains__` 和 `__len__` 魔术方法是否会和 OrderedDict 冲突？
- scraper.py 顶部 import 删除了 OrderedDict，是否影响其他地方？

---

### 修改 3：m-3 修复 — 补反幻觉 + SimHash 单元测试

**问题回顾**：第七轮报告称"反幻觉校验 + SimHash 算法仍 0 单元测试"。

**新建** `tests/test_algorithms.py`，含 5 个测试套件：

**套件 1：TestNormalizeAmount**（7 个测试）
- 金额归一化：万元/亿元/元/小数/纯数字无单位返回 None/空字符串返回 None

**套件 2：TestNormalizeDate**（6 个测试）
- 日期归一化：dash/slash/dot/中文/无效/单位数月日

**套件 3：TestExtractFacts**（3 个测试）
- 事实提取：金额/日期/空文本

**套件 4：TestFactInSource**（4 个测试）
- 事实比对：金额匹配/不匹配/日期归一化匹配/金额等价匹配

**套件 5：TestCheckContent**（3 个测试）
- 整体校验：全通过/检测到幻觉/空原文跳过

**套件 6：TestComputeSimhash**（5 个测试）
- SimHash 计算：返回 int/空文本返回 0/确定性/相似文本汉明距离小/不同文本汉明距离大

**套件 7：TestHammingDistance**（3 个测试）
- 汉明距离：相同为 0/差 1 位为 1/全差为 SIMHASH_BITS

**套件 8：TestIsSimilar**（3 个测试）
- 相似度判断：相同/阈值内/阈值外

**套件 9：TestFindDuplicateInIter**（4 个测试）
- 找重复：存在/不存在/空候选集/相似但非完全相同

**套件 10：TestHostnameLRUCache**（11 个测试）
- LRU 缓存：未命中/命中/覆盖/超容量淘汰/get 后 move-to-end/set 已存在 move-to-end/capacity 0 抛错/clear/contains/len

**审查重点**：
1. 反幻觉金额等价测试（'100万元' vs '1000000元'）是否真的验证了归一化逻辑？
2. SimHash 相似文本测试的汉明距离阈值 10 是否合理？
3. LRU move-to-end 测试是否真正验证了淘汰顺序？
4. 测试覆盖度提升后，企业级成熟度评分能否从 8.4 提升？

---

### 修改 4：新增 HostnameLRUCache 模块 — `app/utils/hostname_cache.py`

完整代码 67 行，独立模块。

**审查重点**：
- 模块独立性：是否依赖 scraper.py？是否有循环 import 风险？
- API 设计：get/set/clear/contains/len 是否够用？
- 错误处理：capacity ≤ 0 抛 ValueError，其他场景是否需要 try/except？

---

## 当前测试状态

```
修改前：117 passed in 293.48s
修改后：170 passed in 295.50s（+53 测试，+2s）
```

新增 53 个测试全部通过，无回归。**测试数从 117 → 170，提升 45%**。

## 测试覆盖度提升总结

| 模块 | 上轮状态 | 本轮状态 |
|---|---|---|
| SAVEPOINT 部分事务 | 2 个测试 | 2 个测试（保持） |
| safe_contains / LIKE 转义 | 9 个测试 | 9 个测试（保持） |
| SSRF IP 分类校验 | 8 个测试 | 11 个测试（+3：完整链路/scheme/blacklist） |
| tender_utils 纯函数 | 8 个测试 | 8 个测试（保持） |
| 反幻觉金额归一化 | 0 测试 | 7 个测试（新增） |
| 反幻觉日期归一化 | 0 测试 | 6 个测试（新增） |
| 反幻觉事实提取 | 0 测试 | 3 个测试（新增） |
| 反幻觉事实比对 | 0 测试 | 4 个测试（新增） |
| 反幻觉整体校验 | 0 测试 | 3 个测试（新增） |
| SimHash 计算 | 0 单元测试 | 5 个测试（新增） |
| SimHash 汉明距离 | 0 单元测试 | 3 个测试（新增） |
| SimHash is_similar | 0 单元测试 | 3 个测试（新增） |
| SimHash find_duplicate_in_iter | 0 单元测试 | 4 个测试（新增） |
| HostnameLRUCache | 0 测试 | 11 个测试（新增） |

---

## 命题覆盖预估（请验证）

| 命题硬要求 | 当前覆盖 | 证据 |
|---|---|---|
| 1. LLM 意图解析 5 槽位 | ✅ | `app/llm/parser.py` |
| 2. ≥2 网站 + ≥1 登录态 | ⚠️ 部分 | ccgp+chinabidding 已有；qianlima 登录态待 Sol 移植 session_manager |
| 3. SimHash 去重 | ✅ | 三阶段批量 + SAVEPOINT（**算法层有单元测试覆盖**） |
| 4. 5 字段汇总 + Word 命名 | ✅ | `app/report/docx_generator.py` |
| 5. cron 定时执行 | ✅ | `is_cron_due` + croniter |
| 6. 增量推送 | ⚠️ 部分 | PushLog + SQL NOT EXISTS 完整；push.py 仅日志占位，待 Sol 移植 email_sender |
| 7. 反幻觉校验 | ✅ | 金额/日期归一化（**算法层有单元测试覆盖**） |

---

## 输出格式要求

```
## 第八轮审查报告

### 评分（0-10）
- 代码质量：X.X（上轮 9.2）
- 命题覆盖：X.X（上轮 8.5）
- 企业级成熟度：X.X（上轮 8.4）

### Critical（必须修复）
- [C-X] ...

### Major（建议修复）
- [M-X] ...

### Minor（可选优化）
- [m-X] ...

### 第七轮问题修复验证
- m-1 测试名诚实命名 + 完整链路测试 — 是否真正验证
- m-2 HostnameLRUCache 抽类 — 是否提升可测试性
- m-3 反幻觉 + SimHash 单元测试 — 是否覆盖核心场景

### 新增测试质量评估
- 53 个测试是否真正验证关键场景
- 测试覆盖度提升后企业级成熟度评分

### 命题覆盖验证
- 哪些硬要求已达成
- 哪些未达成，差什么

### 总结
本轮修复整体评价
```
