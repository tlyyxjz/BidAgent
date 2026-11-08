# -*- coding: utf-8 -*-
"""一次性探针：定位全量跑里那条 `ResourceWarning: unclosed database` 的来源。

为什么要有第二版（2026-11-07）：
    第一版用 `traceback.format_stack()`，每个连接都要读源码行，
    全量跑到 20% 用了 19 分钟（预计 95 分钟），不可用。
    本版只记 `文件名:行号`（`sys._getframe()` 走链，不触发 linecache），
    开销降到可接受范围。

用法（必须带 coverage，否则告警不复现）：
    PYTHONPATH=<本文件目录> python -m pytest -q -p sqlite_leak_probe

判读：
    - 打印「连接未 close 就被回收」⇒ 真凶创建栈
    - session 结束仍有残留 ⇒ 进程退出前都没回收
    - 全程无输出 ⇒ 本路径下无泄漏，告警另有来源（见 README 已排除项）
"""
import sqlite3
import sys

_OPEN: dict[int, str] = {}


def _creation_stack(depth: int = 10) -> str:
    """只记 file:line，不读源码，成本远低于 format_stack。"""
    parts = []
    try:
        f = sys._getframe(1)
    except Exception:
        return "<no frame>"
    n = 0
    while f is not None and n < depth:
        parts.append(f"{f.f_code.co_filename}:{f.f_lineno}")
        f = f.f_back
        n += 1
    return " <- ".join(parts)


class TrackedConnection(sqlite3.Connection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        try:
            _OPEN[id(self)] = _creation_stack()
        except Exception:
            pass
        self._probe_closed = False

    def close(self):
        _OPEN.pop(id(self), None)
        self._probe_closed = True
        return super().close()

    def __del__(self):
        try:
            if not getattr(self, "_probe_closed", True):
                stack = _OPEN.pop(id(self), "<no stack>")
                print("\n[SQLITE-LEAK] 连接未 close 即被回收，创建于：\n  " + stack)
        except Exception:
            pass


_orig_connect = sqlite3.connect


def _probe_connect(*args, **kwargs):
    # 调用方自带 factory 时不覆盖，但要记录"绕过了探针"
    if "factory" not in kwargs:
        kwargs["factory"] = TrackedConnection
    return _orig_connect(*args, **kwargs)


def pytest_configure(config):
    sqlite3.connect = _probe_connect
    print("[SQLITE-LEAK] probe installed (v2, cheap stack)")


def pytest_sessionfinish(session, exitstatus):
    sqlite3.connect = _orig_connect
    still = len(_OPEN)
    print("\n[SQLITE-LEAK] session 结束仍有 %d 个连接未 close" % still)
    for i, (_, stack) in enumerate(list(_OPEN.items())[:5]):
        print("--- 残留 #%d ---\n  %s" % (i + 1, stack))
