@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where node >nul 2>nul || (echo Node.js 22 required & pause & exit /b 1)
where py >nul 2>nul && (py -3 jarvis.py & pause & exit /b)
python jarvis.py
pause
