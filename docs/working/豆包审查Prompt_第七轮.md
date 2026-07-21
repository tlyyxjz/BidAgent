# 豆包代码审查 · 第七轮

## 背景

这是 ScrapeFlow 项目（超聚变命题 · 招投标信息聚合工具）的**第七轮审查**。

- **第六轮**：你给出 0 Critical + 0 Major + 3 Minor，评分 9.1/8.5/8.1
- **第七轮（本轮）**：GLM-5.2 清理了第六轮 3 个 Minor，并补了 27 个单元测试

请基于本轮**新增/修改的代码**做第七轮审查，重点关注：
1. 第六轮 3 个 Minor 是否彻底清理
2. 新增 27 个单元测试是否真正验证了关键场景
3. `_is_ip_blocked` 判断顺序调整是否引入新问题
4. 测试覆盖度提升后，企业级成熟度评分能否提升

命题硬要求（6+1 项）：
1. LLM 意图解析 5 槽位 / 2. ≥2 网站 + ≥1 登录态 / 3. SimHash 去重 / 4. 5 字段汇总+Word 命名 / 5. cron 定时 / 6. 增量推送 / 7. 反幻觉

---

## 本轮修改清单（共 4 项）

### 修改 1：m-1 修复 — scraper.py urlparse / OrderedDict 提到顶部

```python
# 修改前（函数内延迟 import）
async def _scrape_with_playwright(self, ...):
    ...
    from urllib.parse import urlparse  # 每次调用都查 sys.modules
    from collections import OrderedDict
    _hostname_cache: OrderedDict[...] = OrderedDict()
    ...

# 修改后（顶部一次性 import）
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

# 函数内直接使用
_hostname_cache: OrderedDict[str, tuple[bool, str]] = OrderedDict()
```

**审查重点**：是否还有其他延迟 import 未清理？

---

### 修改 2：m-2 修复 — url_safety.py 重构 _is_ip_blocked 接收 ip 对象

```python
# 修改前：接收字符串，内部解析
def _is_ip_blocked(hostname: str) -> tuple[bool, str]:
    try:
        ip = ipaddress.ip_address(hostname)  # 内层解析
    except ValueError:
        return False, ""
    ...

# 修改后：接收 ip 对象，调用方解析
def _is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[bool, str]:
    """调用方负责解析，避免外层 + 内层两次 ipaddress.ip_address 解析。"""
    ...

# is_safe_url 主函数也对应调整
try:
    ip_obj = ipaddress.ip_address(hostname_lower)  # 一次解析
    blocked, reason = _is_ip_blocked(ip_obj)       # 直接传 ip 对象
    if blocked:
        return False, reason
except ValueError:
    # 域名形式：走 DNS 解析
    blocked, reason = _check_dns_records(hostname_lower)
```

**附带修复 m-3（第七轮发现）**：调整判断顺序，更具体的类别优先：

```python
def _is_ip_blocked(ip):
    """m-3 修复：Python 3.13 的 ipaddress 对 127.0.0.1 同时返回
    is_private=True 和 is_loopback=True，先判断 is_loopback 才能给出精确日志。
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_ip_blocked(ip.ipv4_mapped)  # 先解包

    # 顺序：更具体的类别优先
    if ip.is_loopback: return True, f"loopback ip blocked: {ip}"
    if ip.is_link_local: return True, f"link-local ip blocked: {ip}"
    if ip.is_multicast: return True, f"multicast ip blocked: {ip}"
    if ip.is_reserved: return True, f"reserved ip blocked: {ip}"
    if ip.is_private: return True, f"private ip blocked: {ip}"
    return False, ""
```

**审查重点**：
- 判断顺序调整是否影响功能？（127.0.0.1 现在被识别为 loopback 而非 private）
- IPv6 mapped IPv4 解包从末尾移到开头，逻辑是否等价？
- `_check_dns_records` 内部也调用 `_is_ip_blocked(ip)`，签名变更是否兼容？

---

### 修改 3：m-3 修复 — scraper.py 子资源缓存未命中加 debug 日志

```python
# 修改前：缓存未命中且校验通过时无日志
safe, reason = await is_safe_url_async(req_url)
_cache_set(hostname_lower, (safe, reason))
if not safe:
    logger.warning(...)
    await route.abort("blockedbyclient")
    return

# 修改后：对称日志
safe, reason = await is_safe_url_async(req_url)
_cache_set(hostname_lower, (safe, reason))
if safe:
    logger.debug(
        "SSRF guard asset first-check ok hostname=%s url=%s",
        hostname_lower, req_url[:80],
    )
else:
    logger.warning(...)
    await route.abort("blockedbyclient")
    return
```

**审查重点**：debug 级日志是否会污染生产环境日志？（默认日志级别是 INFO）

---

### 修改 4：新增 27 个单元测试 — `tests/test_enterprise_quality.py`

**测试套件 1：TestSavepointPartialSuccess**（2 个测试）
- `test_savepoint_isolates_platform_failure`: 3 个平台，平台 2 故意触发 IntegrityError，验证只有平台 1 + 3 落盘
- `test_savepoint_all_success_commits_all`: 3 个平台都成功，验证全部落盘

**测试套件 2：TestSafeContainsEscape**（9 个测试）
- `test_escape_like_escapes_percent`: "100%" → "100\\%"
- `test_escape_like_escapes_underscore`: "test_name" → "test\\_name"
- `test_escape_like_escapes_backslash`: "a\\b" → "a\\\\b"
- `test_escape_like_handles_empty`: 空字符串不变
- `test_escape_like_preserves_normal_text`: 中文/英文不变
- `test_safe_contains_generates_escape_clause`: 编译后 SQL 含 ESCAPE 关键字
- `test_safe_like_generates_escape_clause`: 同上
- `test_like_escape_constant_is_backslash`: LIKE_ESCAPE == "\\"

**测试套件 3：TestIsSafeUrlBlocksInternal**（8 个测试）
- `test_loopback_ipv4_blocked`: 127.0.0.1 拒绝
- `test_private_ipv4_blocked`: 10.0.0.1 / 172.16.0.1 / 192.168.1.1 拒绝
- `test_link_local_blocked`: 169.254.169.254（云元数据）拒绝
- `test_ipv6_loopback_blocked`: ::1 拒绝
- `test_ipv6_mapped_ipv4_loopback_blocked`: ::ffff:127.0.0.1 拒绝
- `test_public_ip_allowed`: 8.8.8.8 允许
- `test_blocked_hostname_in_blacklist`: localhost / metadata.google.internal 在黑名单
- `test_is_safe_url_rejects_loopback_url`: 端到端验证

**测试套件 4：TestTenderUtilsPureFunctions**（8 个测试）
- `_parse_decimal`: 万/亿元单位换算、逗号处理、无效输入返回 None
- `_infer_platform`: URL 推断、template 优先级
- `_hash_contact`: SHA256 哈希、空输入返回 None
- `_build_tender`: 字段映射、联系人哈希

**审查重点**：
1. SAVEPOINT 测试是否真正验证了"部分成功语义"？
2. safe_contains 测试通过编译 SQL 验证 ESCAPE 关键字，这种方式是否可靠？
3. is_safe_url 测试绕过 conftest mock 的方式（直接调 `_is_ip_blocked`）是否合理？
4. 测试覆盖度：27 个新测试是否覆盖了之前 0 测试的关键模块？
5. 还有哪些关键场景没覆盖？（如 hostname LRU 缓存的淘汰逻辑、collector 并发抓取）

---

## 当前测试状态

```
修改前：90 passed in 277.33s
修改后：117 passed in 293.48s（+27 测试，+16s）
```

新增 27 个测试全部通过，原有 90 个测试无回归。

---

## 命题覆盖预估（请验证）

| 命题硬要求 | 当前覆盖 | 证据 |
|---|---|---|
| 1. LLM 意图解析 5 槽位 | ✅ | `app/llm/parser.py` |
| 2. ≥2 网站 + ≥1 登录态 | ⚠️ 部分 | ccgp+chinabidding 已有；qianlima 登录态待 Sol |
| 3. SimHash 去重 | ✅ | 三阶段批量 + SAVEPOINT（测试验证） |
| 4. 5 字段汇总 + Word 命名 | ✅ | `app/report/docx_generator.py` |
| 5. cron 定时执行 | ✅ | `is_cron_due` + croniter |
| 6. 增量推送 | ⚠️ 部分 | PushLog + SQL NOT EXISTS；push.py 待 Sol 移植 |
| 7. 反幻觉校验 | ✅ | 金额/日期归一化 + 正则单一来源 |

---

## 输出格式要求

```
## 第七轮审查报告

### 评分（0-10）
- 代码质量：X.X（上轮 9.1）
- 命题覆盖：X.X（上轮 8.5）
- 企业级成熟度：X.X（上轮 8.1）

### Critical（必须修复）
- [C-X] ...

### Major（建议修复）
- [M-X] ...

### Minor（可选优化）
- [m-X] ...

### 第六轮问题修复验证
- m-1 延迟 import 清理 — 是否彻底
- m-2 _is_ip_blocked 签名重构 — 是否引入新问题
- m-3 子资源缓存 debug 日志 — 是否合理

### 新增测试质量评估
- 27 个测试是否真正验证关键场景
- 测试覆盖度提升后企业级成熟度评分

### 命题覆盖验证
- 哪些硬要求已达成
- 哪些未达成，差什么

### 总结
本轮修复整体评价
```
