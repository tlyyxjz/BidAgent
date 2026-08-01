# -*- coding: utf-8 -*-
"""修复最后一处残留"""
import os
import shutil

SRC = r"C:\Users\Lenovo\Desktop\BidAgent\_w2_report\GOAI_初赛提交清单.md"
DEST = r"C:\Users\Lenovo\Desktop\标小智_GOAI初赛提交\01_必交材料\GOAI_初赛提交清单.md"

with open(SRC, "r", encoding="utf-8") as f:
    content = f.read()
original = content

# 修复本地路径 + 旧测试数
content = content.replace(
    "| 项目代码 | `C:\\Users\\Lenovo\\Desktop\\BidAgent\\` | ✅ 571 测试通过 |",
    "| 项目代码 | GitHub 仓库 tlyyxjz/BidAgent | ✅ 826 测试通过 |"
)

if content != original:
    with open(SRC, "w", encoding="utf-8") as f:
        f.write(content)
    shutil.copy2(SRC, DEST)
    print("FIXED + SYNCED")
else:
    print("NO CHANGE")

# 最终验证
print("\n=== 最终验证 ===")
with open(DEST, "r", encoding="utf-8") as f:
    lines = f.readlines()
remaining = 0
for i, line in enumerate(lines):
    if "BidAgent" in line and "github" not in line.lower() and "tlyyxjz" not in line.lower():
        remaining += 1
        print(f"  L{i+1}: {line.strip()[:70]}")
if remaining == 0:
    print("  ALL OK - 仅 GitHub URL 保留 BidAgent")
