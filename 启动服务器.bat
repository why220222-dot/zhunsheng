@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
echo 启动「准绳」绩效评审智能体...
start "" /b %PY% server.py --port 8899
timeout /t 3 >nul
start "" http://127.0.0.1:8899
echo.
echo 已在后台启动，地址 http://127.0.0.1:8899
echo 关闭本窗口不影响服务运行。
echo 停止服务请运行「停止服务器.bat」。
