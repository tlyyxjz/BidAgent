# -*- coding: utf-8 -*-
"""修复提交材料中的过期口径，同步到提交文件夹"""
import os
import shutil
import re

SRC = r"C:\Users\Lenovo\Desktop\BidAgent"
DEST = r"C:\Users\Lenovo\Desktop\标小智_GOAI初赛提交"

fixes = []

# === 1. GOAI_初赛提交材料_正式版.md ===
f1 = os.path.join(SRC, "GOAI_初赛提交材料_正式版.md")
with open(f1, "r", encoding="utf-8") as f:
    content = f.read()
original = content

# 标题品牌名
content = content.replace("# GOAI 2026 初赛提交材料 - BidAgent 智能标讯助手", "# GOAI 2026 初赛提交材料 - 标小智")
# 测试数
content = content.replace("571 passed, 1 skipped", "826 passed, 1 skipped")

if content != original:
    with open(f1, "w", encoding="utf-8") as f:
        f.write(content)
    fixes.append(("GOAI_初赛提交材料_正式版.md", "品牌名+测试数"))
    print(f"FIXED: {f1}")

# === 2. BidAgent_Demo_脚本.md ===
f2 = os.path.join(SRC, "BidAgent_Demo_脚本.md")
with open(f2, "r", encoding="utf-8") as f:
    content = f.read()
original = content

# 571 -> 826 (3处)
content = content.replace("571 项测试通过", "826 项测试通过")
content = content.replace("571 项测试通过", "826 项测试通过")
content = content.replace("571 tests passing", "826 tests passing")
# 20 类 -> 32 类
content = content.replace("20 类品类基准价格库", "32 类品类基准价格库")
# 三维加权 -> 五维度加权
content = content.replace("活跃度、中标率、报价偏离度三维加权", "集中度、金额、频率、地域、采购人五维度加权（AHP 层次分析法导出）")
# 供应商信用评分卡片的三维度展示改为五维度
content = content.replace("活跃度 85 · 中标率 72 · 报价偏离度 68 · 综合 75.2/100", "集中度 82 · 金额 75 · 频率 88 · 地域 70 · 采购人 80 · 综合 79.0/100")

if content != original:
    with open(f2, "w", encoding="utf-8") as f:
        f.write(content)
    fixes.append(("BidAgent_Demo_脚本.md", "测试数+BOQ类数+维度口径"))
    print(f"FIXED: {f2}")

# === 3. Demo录制脚本_3分钟.md ===
f3 = os.path.join(SRC, "docs", "Demo录制脚本_3分钟.md")
with open(f3, "r", encoding="utf-8") as f:
    content = f.read()
original = content

# 200 项 -> 826 项
content = content.replace("200 项测试通过，覆盖率 97%", "826 项测试通过，覆盖率 97%")
content = content.replace("200 项 pytest 全通过，覆盖率 97%", "826 项 pytest 全通过，覆盖率 97%")
# 571 项 -> 826 项
content = content.replace("571 项", "826 项")
# 200 项 / 97% 覆盖 -> 826 项 / 97% 覆盖
content = content.replace("200 项 / 97% 覆盖", "826 项 / 97% 覆盖")

if content != original:
    with open(f3, "w", encoding="utf-8") as f:
        f.write(content)
    fixes.append(("Demo录制脚本_3分钟.md", "测试数571/200->826"))
    print(f"FIXED: {f3}")

# === 同步到提交文件夹 ===
print("\n=== 同步到提交文件夹 ===")
sync_files = [
    (f1, os.path.join(DEST, "01_核心提交材料", "GOAI_初赛提交材料_正式版.md")),
    (f2, os.path.join(DEST, "03_Demo与路演", "BidAgent_Demo_脚本.md")),
    (f3, os.path.join(DEST, "03_Demo与路演", "Demo录制脚本_3分钟.md")),
]
for src, dest in sync_files:
    shutil.copy2(src, dest)
    print(f"SYNCED: {os.path.basename(src)} -> {os.path.relpath(dest, DEST)}")

# === 验证 ===
print("\n=== 验证修复结果 ===")
verify_files = [
    (os.path.join(DEST, "01_核心提交材料", "GOAI_初赛提交材料_正式版.md"), ["571", "BidAgent 智能标讯"]),
    (os.path.join(DEST, "03_Demo与路演", "BidAgent_Demo_脚本.md"), ["571", "20 类", "三维加权"]),
    (os.path.join(DEST, "03_Demo与路演", "Demo录制脚本_3分钟.md"), ["571", "200 项", "200项"]),
]
all_ok = True
for fpath, bad_keywords in verify_files:
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    fname = os.path.basename(fpath)
    for kw in bad_keywords:
        if kw in content:
            print(f"  STILL HAS '{kw}': {fname}")
            all_ok = False
    # 检查新值是否存在
    if "826" not in content:
        print(f"  MISSING '826': {fname}")
        all_ok = False

if all_ok:
    print("  ALL OK - 过期口径已全部清除")
else:
    print("  ISSUES REMAIN")

print(f"\n修复文件数: {len(fixes)}")
for name, desc in fixes:
    print(f"  {name}: {desc}")
