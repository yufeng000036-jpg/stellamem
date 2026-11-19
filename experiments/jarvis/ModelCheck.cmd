@echo off
call "%~dp0Start.cmd" --model-check
exit /b %errorlevel%
