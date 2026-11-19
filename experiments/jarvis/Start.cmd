@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
where python >nul 2>nul
if errorlevel 1 goto pylauncher
set "runner=python"
python -c "import sqlite3,sys; sys.exit(int(sys.version_info < (3,10)))" >nul 2>nul
if not errorlevel 1 goto nodecheck
:pylauncher
where py >nul 2>nul
if errorlevel 1 goto missing
set "runner=py -3"
%runner% -c "import sqlite3,sys; sys.exit(int(sys.version_info < (3,10)))" >nul 2>nul
if errorlevel 1 goto missing
:nodecheck
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)" >nul 2>nul
if errorlevel 1 goto node_missing
if /i "%~1"=="--verify" goto verify
if /i "%~1"=="--model-check" goto modelcheck
%runner% -B jarvis.py %*
goto end
:modelcheck
%runner% -B model_check.py
goto end
:verify
%runner% -B local_check.py
goto end
:missing
echo 请先安装或修复 Python 3.10 以上版本，推荐 3.12，需包含 SQLite。无需管理员权限。
goto failed
:node_missing
echo 缺少 Node.js 22 或更新版本。安装后请重新打开窗口。
:failed
pause
exit /b 1
:end
set "result=%ERRORLEVEL%"
if "%JARVIS_BATCH_TEST%"=="1" exit /b %result%
pause
exit /b %result%
