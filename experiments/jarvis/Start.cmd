@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "runner="
set "pyflag="
if defined JARVIS_PYTHON (
    set "candidate=%JARVIS_PYTHON%"
    call :probe
    if errorlevel 1 goto missing
    goto nodecheck
)
set "candidate=E:\Python\python.exe"
call :probe
if defined runner goto nodecheck
for %%P in (python.exe) do set "candidate=%%~$PATH:P"
call :probe
if defined runner goto nodecheck
for %%P in (py.exe) do set "candidate=%%~$PATH:P"
set "pyflag=-3"
call :probe
if not defined runner goto missing
goto nodecheck
:probe
if not defined candidate exit /b 1
if not exist "%candidate%" exit /b 1
"%candidate%" %pyflag% -c "import sqlite3,sys; sys.exit(int(sys.version_info < (3,10)))" >nul 2>nul
if errorlevel 1 exit /b 1
set "runner=%candidate%"
exit /b 0
:nodecheck
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)" >nul 2>nul
if errorlevel 1 goto node_missing
if /i "%~1"=="--verify" goto verify
if /i "%~1"=="--model-check" goto modelcheck
"%runner%" %pyflag% -B jarvis.py %*
goto end
:modelcheck
"%runner%" %pyflag% -B model_check.py
goto end
:verify
"%runner%" %pyflag% -B local_check.py
goto end
:missing
echo 未找到可用的 Python 3.10 以上版本及 SQLite。已装在其他位置时，可用 JARVIS_PYTHON 指定 python.exe 的完整路径。
goto failed
:node_missing
echo 缺少 Node.js 22 或更新版本。安装后请重新打开窗口。
:failed
if "%JARVIS_BATCH_TEST%"=="1" exit /b 1
pause
exit /b 1
:end
set "result=%ERRORLEVEL%"
if "%JARVIS_BATCH_TEST%"=="1" exit /b %result%
pause
exit /b %result%
