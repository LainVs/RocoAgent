@echo off
rem ============================================================
rem  RocoAgent front-end launcher  (double-click / desktop shortcut)
rem
rem  IMPORTANT: this MUST use the system Python (C:\Python314),
rem  not the project .venv.
rem
rem    .venv        has bleak      (bot needs it)   no pywebview
rem    system py    has pywebview  (overlay needs it) no bleak
rem
rem  ui.py passes sys.executable down to overlay.py, so running
rem  it with the .venv silently produces "no window".
rem  See docs/STARTUP.md section 1.1.
rem ============================================================
setlocal
chcp 65001 >nul

set "ROOT=%~dp0.."
set "PY=C:\Python314\python.exe"

cd /d "%ROOT%"

if not exist "%PY%" (
  echo [ERROR] system python not found:
  echo         %PY%
  echo.
  echo Edit scripts\start_ui.cmd and point PY at the interpreter
  echo that has pywebview installed.
  pause
  exit /b 2
)

rem Quick pre-check: without pywebview the overlay will not appear.
"%PY%" -c "import webview" >nul 2>&1
if errorlevel 1 (
  echo [WARN] pywebview is missing in %PY%
  echo        The console will still open, but the overlay will not.
  echo        Fix: "%PY%" -m pip install pywebview
  echo.
)

if "%~1"=="" (
  "%PY%" src\ui.py --overlay --console
) else (
  "%PY%" src\ui.py %*
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] ui.py exited with code %RC%
  pause
  exit /b %RC%
)

rem Server + overlay run detached, so this window can close.
exit /b 0
