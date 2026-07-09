@echo off
chcp 65001 >nul
cd /d "%~dp0"
title grokcli-2api

echo.
echo  === grokcli-2api ===
echo  Working dir: %CD%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)

python -c "import fastapi,uvicorn,httpx" 2>nul
if errorlevel 1 (
  echo Installing dependencies...
  pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
  )
)

REM 默认自动打开浏览器；设 GROK2API_OPEN_BROWSER=0 可关闭
if not defined GROK2API_OPEN_BROWSER set GROK2API_OPEN_BROWSER=1
if not defined GROK2API_HOST set GROK2API_HOST=127.0.0.1
if not defined GROK2API_PORT set GROK2API_PORT=3000

echo Starting grokcli-2api on http://%GROK2API_HOST%:%GROK2API_PORT% ...
echo Admin: http://127.0.0.1:%GROK2API_PORT%/admin
echo.

python app.py
set EXITCODE=%ERRORLEVEL%
if not %EXITCODE%==0 (
  echo.
  echo [ERROR] 服务退出，代码 %EXITCODE%
  pause
)
exit /b %EXITCODE%
