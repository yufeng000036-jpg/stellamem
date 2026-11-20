@echo off
call "%~dp0Start.cmd" --verify
exit /b %errorlevel%
