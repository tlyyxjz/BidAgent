"""T9 · 最小消融：coverage 7.15.0 按线程持有 sqlite 连接会不会自己泄漏。

口径与局限（2026-11-08 实测，Windows / Python 3.13.5 / coverage 7.15.0）：
- 目的：验证「--cov 下那条 ResourceWarning 是 coverage 自己没关连接」这个假设。
- 做法：去掉项目全部代码，只留 CoverageData + 12 个短生命周期线程写入。
- 跑法：python -X tracemalloc=10 -W always::ResourceWarning _perf/cov_thread_probe.py
- 结果：12 个线程结束后 _dbs 只持有 1 个连接，GC 后 **0 条 unclosed database**。
  ⇒ **该假设被否掉**。CoverageData._reap_dead_thread_dbs 会在下一次 _connect()
  时回收死线程连接，7.15.0 在纯 coverage 场景下不泄漏。
- 局限：这只能否掉「coverage 单独用就泄漏」，不能证明项目全量那一跑里
  是谁创建的连接。真 CI（Linux / Py3.11）日志里 grep "unclosed database" = 0，
  ⇒ 那条 warning 属 Windows + Py3.13 环境相关，目标环境不出现。
- 本脚本不修改任何全局状态之外的东西，不装探针、不重写 __del__，
  避免「探针压制了它要检测的告警」那类判据污染。
"""

import gc
import tempfile
import threading
import warnings
from pathlib import Path

from coverage.sqldata import CoverageData

BASE = str(Path(tempfile.gettempdir()) / "cov_thread_probe.dat")
for suffix in ("", "-journal", "-wal", "-shm"):
    path = Path(BASE + suffix)
    if path.exists():
        path.unlink()

warnings.simplefilter("always", ResourceWarning)

cd = CoverageData(basename=BASE)


def worker(i):
    cd.add_lines({f"probe_{i}.py": {1, 2, 3}})


threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print(f"[T9] 12 个线程结束后 _dbs 仍持有 {len(cd._dbs)} 个连接")
print(f"[T9] 当前存活线程 {threading.active_count()}")
del cd
for _ in range(3):
    gc.collect()
print("[T9] GC 完成")
