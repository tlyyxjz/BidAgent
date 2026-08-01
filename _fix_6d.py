# -*- coding: utf-8 -*-
"""修复 Demo录制脚本_3分钟.md 的 6 维度旧口径"""
import os
import shutil

SRC = r"C:\Users\Lenovo\Desktop\BidAgent\docs\Demo录制脚本_3分钟.md"
DEST = r"C:\Users\Lenovo\Desktop\标小智_GOAI初赛提交\03_Demo与路演\Demo录制脚本_3分钟.md"

with open(SRC, "r", encoding="utf-8") as f:
    content = f.read()
original = content

# 6 维度 -> 5 维度
content = content.replace("6 维度信用评分卡（参与项目数/总金额/中标率/近期活跃/金额属实/类型覆盖）",
                          "5 维度信用评分卡（集中度/金额/频率/地域/采购人）")
content = content.replace("六维度信用评分", "五维度信用评分")
content = content.replace("6 维度信用评分 + 雷达图", "5 维度信用评分 + 雷达图")

if content != original:
    with open(SRC, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"FIXED: {SRC}")
    # 同步到提交文件夹
    shutil.copy2(SRC, DEST)
    print(f"SYNCED to submission folder")
else:
    print("NO CHANGES NEEDED")

# 验证
with open(DEST, "r", encoding="utf-8") as f:
    v = f.read()
if "6 维度" in v or "六维度" in v:
    print("STILL HAS 6 维度!")
elif "5 维度" in v or "五维度" in v:
    print("OK: 5 维度 已更新")
