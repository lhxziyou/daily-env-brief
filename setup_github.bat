@echo off
chcp 65001 >nul
echo ==========================================
echo   每日环保简报 - GitHub 仓库推送向导
echo ==========================================
echo.
echo 你需要先准备：
echo  1. GitHub 用户名（不是邮箱，是你主页 github.com/xxx 里的 xxx）
echo  2. GitHub Personal Access Token（classic，需勾选 repo 权限）
echo     获取地址：https://github.com/settings/tokens/new
echo.
pause
powershell -ExecutionPolicy Bypass -File "%~dp0setup_github.ps1"
pause
