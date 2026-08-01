# -*- coding: utf-8 -*-
"""检查提交文件夹的材料是否与源文件最新版本一致，自动更新旧版本"""
import os
import hashlib
import shutil

SRC = r"C:\Users\Lenovo\Desktop\BidAgent"
DEST = r"C:\Users\Lenovo\Desktop\标小智_GOAI初赛提交"

FILES = [
    (r"GOAI_初赛提交材料_正式版.md", r"01_核心提交材料\GOAI_初赛提交材料_正式版.md"),
    (r"_w2_report\proposal.pptx", r"01_核心提交材料\proposal.pptx"),
    (r"_w2_report\GOAI_初赛提交清单.md", r"01_核心提交材料\GOAI_初赛提交清单.md"),
    (r"_w2_report\W2_评测报告.md", r"02_评测与质量报告\W2_评测报告.md"),
    (r"_w2_report\K3_交付物复查报告.md", r"02_评测与质量报告\K3_交付物复查报告.md"),
    (r"_w2_report\K3_代码review报告.md", r"02_评测与质量报告\K3_代码review报告.md"),
    (r"_w2_report\K3_测试盲点报告.md", r"02_评测与质量报告\K3_测试盲点报告.md"),
    (r"_w2_report\K3_审校报告.md", r"02_评测与质量报告\K3_审校报告.md"),
    (r"docs\Demo录制脚本_3分钟.md", r"03_Demo与路演\Demo录制脚本_3分钟.md"),
    (r"BidAgent_Demo_脚本.md", r"03_Demo与路演\BidAgent_Demo_脚本.md"),
    (r"docs\路演讲稿.md", r"03_Demo与路演\路演讲稿.md"),
    (r"docs\路演讲稿_改动说明.md", r"03_Demo与路演\路演讲稿_改动说明.md"),
    (r"_w2_report\compliance.md", r"04_开源与合规\compliance.md"),
    (r"README.md", r"04_开源与合规\README.md"),
    (r"CHANGELOG.md", r"04_开源与合规\CHANGELOG.md"),
    (r"CONTRIBUTING.md", r"04_开源与合规\CONTRIBUTING.md"),
    (r"DEPLOY.md", r"04_开源与合规\DEPLOY.md"),
    (r"docs\credit_score_methodology.md", r"04_开源与合规\credit_score_methodology.md"),
    (r"LICENSE", r"04_开源与合规\LICENSE"),
]

def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

print("=" * 70)
print("文件版本一致性检查")
print("=" * 70)
print(f"{'状态':<10} {'源mtime':<18} {'副本mtime':<18} {'文件名'}")
print("-" * 70)

ok = 0
outdated = 0
missing = 0
updated = 0

for src_rel, dest_rel in FILES:
    src_path = os.path.join(SRC, src_rel)
    dest_path = os.path.join(DEST, dest_rel)
    sname = os.path.basename(src_path)

    if not os.path.exists(src_path):
        print(f"{'MISSING-SRC':<10} {'-':<18} {'-':<18} {sname}")
        missing += 1
        continue
    if not os.path.exists(dest_path):
        # 副本不存在，直接复制
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(src_path, dest_path)
        print(f"{'COPIED':<10} {os.path.getmtime(src_path):<18.0f} {'NEW':<18} {sname}")
        updated += 1
        continue

    src_hash = md5(src_path)
    dest_hash = md5(dest_path)
    src_mtime = os.path.getmtime(src_path)
    dest_mtime = os.path.getmtime(dest_path)

    from datetime import datetime
    src_dt = datetime.fromtimestamp(src_mtime).strftime("%m-%d %H:%M")
    dest_dt = datetime.fromtimestamp(dest_mtime).strftime("%m-%d %H:%M")

    if src_hash == dest_hash:
        print(f"{'OK':<10} {src_dt:<18} {dest_dt:<18} {sname}")
        ok += 1
    else:
        print(f"{'OUTDATED':<10} {src_dt:<18} {dest_dt:<18} {sname}")
        # 自动更新
        shutil.copy2(src_path, dest_path)
        print(f"{'  -> UPDATED':<10}")
        outdated += 1
        updated += 1

print("-" * 70)
print(f"OK: {ok} | OUTDATED(已更新): {outdated} | MISSING: {missing} | 总计: {len(FILES)}")
print()

# 额外检查：提交文件夹里是否有源文件已删除的多余文件
print("=" * 70)
print("额外检查：提交文件夹是否有源文件不存在的多余文件")
print("=" * 70)
dest_files = set()
for root, dirs, files in os.walk(DEST):
    for f in files:
        rel = os.path.relpath(os.path.join(root, f), DEST)
        dest_files.add(rel)

expected = set(os.path.normpath(d) for _, d in FILES)
extra = dest_files - expected
if extra:
    print(f"发现 {len(extra)} 个多余文件:")
    for f in extra:
        print(f"  EXTRA: {f}")
else:
    print("无多余文件")

print()
print("=" * 70)
print("最终提交文件夹结构:")
print("=" * 70)
for root, dirs, files in os.walk(DEST):
    level = root.replace(DEST, "").count(os.sep)
    indent = "  " * level
    dirname = os.path.basename(root)
    if level == 0:
        print(f"{dirname}/")
    else:
        print(f"{indent}{dirname}/")
    for f in sorted(files):
        size = os.path.getsize(os.path.join(root, f))
        print(f"{indent}  {f}  ({size:,} bytes)")
