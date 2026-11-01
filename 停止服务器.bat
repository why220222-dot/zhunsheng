@echo off
chcp 65001 >nul
echo 停止「准绳」服务（端口 8899）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8899 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
echo 已停止。
pause
