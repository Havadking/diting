@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 同 run_gui.bat，但额外开放局域网访问。地址先在这里算好打印，服务用 pythonw 后台跑，本窗口可以随手关掉
echo 平板/手机打开（端口被占时会顺延 +1）：
python -c "import server; print(chr(10).join('  http://' + ip + ':' + str(server.DEFAULT_PORT) for ip in server.lan_ips()) or '  没找到局域网 IP，请用 ipconfig 查看')"
echo.
start "" pythonw server.py --host 0.0.0.0
echo 服务已在后台启动，浏览器会自动打开。本窗口可以关闭。
pause
