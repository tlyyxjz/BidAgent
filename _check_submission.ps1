$src = "C:\Users\Lenovo\Desktop\BidAgent"
$dest = "C:\Users\Lenovo\Desktop\xiaobiaozhi_GOAI_submission"

# Rename folder if exists
$oldDest = "C:\Users\Lenovo\Desktop\" + [char]0x6807 + [char]0x5c0f + [char]0x667a + "_GOAI" + [char]0x521d + [char]0x8d5b + [char]0x63d0 + [char]0x4ea4
if (Test-Path $oldDest) {
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Rename-Item $oldDest $dest
    Write-Host "Folder renamed to: $dest"
}
if (-not (Test-Path $dest)) {
    New-Item -ItemType Directory -Path $dest | Out-Null
    New-Item -ItemType Directory -Path "$dest\01_core" | Out-Null
    New-Item -ItemType Directory -Path "$dest\02_reports" | Out-Null
    New-Item -ItemType Directory -Path "$dest\03_demo" | Out-Null
    New-Item -ItemType Directory -Path "$dest\04_opensource" | Out-Null
}

$files = @(
    @{src="$src\GOAI_chusai_tijiao_zhengshiban.md"; dest="$dest\01_core\GOAI_chusai_tijiao_zhengshiban.md"; sname="GOAI submission md"},
    @{src="$src\_w2_report\proposal.pptx"; dest="$dest\01_core\proposal.pptx"; sname="proposal.pptx"},
    @{src="$src\_w2_report\GOAI_chusai_tijiao_qingdan.md"; dest="$dest\01_core\GOAI_chusai_tijiao_qingdan.md"; sname="GOAI checklist md"},
    @{src="$src\_w2_report\W2_pingce_baogao.md"; dest="$dest\02_reports\W2_pingce_baogao.md"; sname="W2 report"},
    @{src="$src\_w2_report\K3_jiaofuwu_fucha_baogao.md"; dest="$dest\02_reports\K3_jiaofuwu_fucha_baogao.md"; sname="K3 delivery report"},
    @{src="$src\_w2_report\K3_daima_review_baogao.md"; dest="$dest\02_reports\K3_daima_review_baogao.md"; sname="K3 code review"},
    @{src="$src\_w2_report\K3_ceshi_mangdian_baogao.md"; dest="$dest\02_reports\K3_ceshi_mangdian_baogao.md"; sname="K3 test gaps"},
    @{src="$src\_w2_report\K3_shenxiao_baogao.md"; dest="$dest\02_reports\K3_shenxiao_baogao.md"; sname="K3 review"},
    @{src="$src\docs\Demo_luzhi_jiaoben_3fenzhong.md"; dest="$dest\03_demo\Demo_luzhi_jiaoben_3fenzhong.md"; sname="Demo 3min script"},
    @{src="$src\BidAgent_Demo_jiaoben.md"; dest="$dest\03_demo\BidAgent_Demo_jiaoben.md"; sname="Demo 90s script"},
    @{src="$src\docs\luyan_jianggao.md"; dest="$dest\03_demo\luyan_jianggao.md"; sname="pitch script"},
    @{src="$src\docs\luyan_jianggao_gaidong_shuoming.md"; dest="$dest\03_demo\luyan_jianggao_gaidong_shuoming.md"; sname="pitch changes"},
    @{src="$src\_w2_report\compliance.md"; dest="$dest\04_opensource\compliance.md"; sname="compliance"},
    @{src="$src\README.md"; dest="$dest\04_opensource\README.md"; sname="README"},
    @{src="$src\CHANGELOG.md"; dest="$dest\04_opensource\CHANGELOG.md"; sname="CHANGELOG"},
    @{src="$src\CONTRIBUTING.md"; dest="$dest\04_opensource\CONTRIBUTING.md"; sname="CONTRIBUTING"},
    @{src="$src\DEPLOY.md"; dest="$dest\04_opensource\DEPLOY.md"; sname="DEPLOY"},
    @{src="$src\docs\credit_score_methodology.md"; dest="$dest\04_opensource\credit_score_methodology.md"; sname="credit methodology"},
    @{src="$src\LICENSE"; dest="$dest\04_opensource\LICENSE"; sname="LICENSE"}
)
