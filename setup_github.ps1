# 一键创建 GitHub 仓库并推送（PowerShell 5.1 兼容）
$ErrorActionPreference = "Stop"

$githubUser = Read-Host "请输入你的 GitHub 用户名（默认 lhxziyou，直接回车采用）"
if ([string]::IsNullOrWhiteSpace($githubUser)) {
    $githubUser = "lhxziyou"
}
Write-Host "将使用 GitHub 用户名: $githubUser" -ForegroundColor Cyan

$token = Read-Host "请输入 GitHub Personal Access Token（classic，需勾选 repo 权限）" -AsSecureString
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
$plainToken = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

$repoName = "daily-env-brief"
$repoDesc = "每日环保简报 - 双线推送（GitHub Actions + PushPlus / WorkBuddy + WxPusher）"

# 1. 创建仓库（若已存在则跳过）
Write-Host "正在 GitHub 创建仓库 $repoName ..." -ForegroundColor Cyan
$headers = @{
    "Authorization" = "token $plainToken"
    "Accept" = "application/vnd.github.v3+json"
}
$body = @{
    name = $repoName
    description = $repoDesc
    private = $false
    auto_init = $false
} | ConvertTo-Json -Compress

try {
    $resp = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body -ContentType "application/json"
    Write-Host "仓库创建成功: $($resp.html_url)" -ForegroundColor Green
} catch {
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode.value__ -eq 422) {
        Write-Host "仓库可能已存在，继续配置 remote..." -ForegroundColor Yellow
    } else {
        Write-Host "创建仓库失败: $_" -ForegroundColor Red
        exit 1
    }
}

# 2. 设置 remote
$remoteUrl = "https://$plainToken@github.com/$githubUser/$repoName.git"
git remote remove origin 2>$null
git remote add origin $remoteUrl

# 3. 推送到 GitHub
Write-Host "正在推送代码..." -ForegroundColor Cyan
git branch -M main
git push -u origin main

Write-Host "完成！仓库地址: https://github.com/$githubUser/$repoName" -ForegroundColor Green
