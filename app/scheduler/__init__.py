"""任务调度模块。

asyncio 定时循环 + 增量推送 + SimHash 去重。

实现说明：
- 定时调度由 app/scheduler_loop.py 的 asyncio 循环实现（SCAN_INTERVAL_SECONDS=60）
- 非 APScheduler（requirements.txt 含 APScheduler 但当前未实际使用）
- APP_ROLE=scheduler 时由 Docker 容器独立运行

命题硬要求：
- 支持每日/每周定时推送
- 已推送内容不重复推送（SimHash 海明距离 ≤3 视为相似）
"""
