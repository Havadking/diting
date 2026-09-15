@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 同 run_gui.bat，但额外开放局域网访问，平板/手机可用控制台里打印的地址打开
python server.py --host 0.0.0.0
