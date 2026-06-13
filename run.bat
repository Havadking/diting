@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   东方财富股吧用户监控  (按 Ctrl+C 停止)
echo ============================================
python monitor.py
echo.
echo 程序已退出。
pause
