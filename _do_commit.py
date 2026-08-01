# -*- coding: utf-8 -*-
"""1. 更新 .gitignore 排除临时文件
2. 修改 README 标题为标小智
3. git add + commit + push"""
import os
import subprocess

SRC = r"C:\Users\Lenovo\Desktop\BidAgent"
os.chdir(SRC)

# === 1. 更新 .gitignore ===
gi_path = os.path.join(SRC, ".gitignore")
with open(gi_path, "r", encoding="utf-8") as f:
    gi = f.read()

addition = """
# ==== 临时脚本与备份（不应入库）====
_*.py
_*.ps1
_*.txt
_tmp_*/
*.pptx.bak
*.pptx.bak2
*_backup_*/
_w2_report/_sol_verify*.py
_w2_report/_writetest.tmp
_w2_report/proposal*.bak*
_w2_report/screenshot_*.png
demo-screenshots/
_ppt_inspection_report.txt
"""

if "_*.py" not in gi:
    with open(gi_path, "a", encoding="utf-8") as f:
        f.write(addition)
    print("UPDATED: .gitignore")
else:
    print("SKIP: .gitignore already has temp file rules")

# === 2. 修改 README 标题 ===
readme_path = os.path.join(SRC, "README.md")
with open(readme_path, "r", encoding="utf-8") as f:
    readme = f.read()
original = readme

readme = readme.replace("# BidAgent — 智能标讯助手", "# 标小智 — 智能标讯助手")
readme = readme.replace("AI+金融方向的招投标数据服务系统", "AI+金融方向的招投标数据服务系统 · GOAI 2026")

if readme != original:
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    print("UPDATED: README.md title -> 标小智")

# === 3. git 操作 ===
def run(cmd, check=True):
    print(f"\n>>> {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=SRC)
    if r.stdout:
        print(r.stdout[:2000])
    if r.stderr:
        print(r.stderr[:1000])
    if check and r.returncode != 0:
        print(f"ERROR: exit code {r.returncode}")
    return r

# git status
run("git status --short")

# git add（排除临时文件已被 .gitignore 处理）
run("git add -A")

# 看看暂存了什么
run("git diff --cached --stat")

# commit
commit_msg = """feat(W4): 品牌统一为标小智 + GOAI初赛提交材料修复

- README/PPT/合规声明/提交材料品牌名 BidAgent -> 标小智
- 删除 T7 知识增强规划页,改为 LLM 工程化能力页
- 新增 LICENSE (Apache 2.0, 标小智团队)
- BOQ 扩展 20->32 类(补充工程类+IT服务类)
- 评分维度统一为 5 维度(AHP 层次分析法导出)
- 采集进度 API + 工作台采集进度卡片
- 金融分析章节写入 Word 报告
- 移除哈希 mock fallback, 未命中时诚实降级
- 新增 22 项测试(finance/collector/boq), 总计 848 passed
- 敏感数据脱敏(手机号/测试secret)
- .gitignore 排除临时脚本和备份文件"""

run(f'git commit -m "{commit_msg}"')

# push
run("git push origin feature/glm-w4-k3-data")

print("\n=== DONE ===")
