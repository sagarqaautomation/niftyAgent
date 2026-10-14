@echo off
setlocal EnableExtensions

REM Run this file from anywhere; the repository root is the folder containing this BAT.
set "REPO_DIR=%~dp0"
set "APP_DIR=%REPO_DIR%nifty_intraday_agent_complete"
set "PYTHON=%REPO_DIR%.venv\Scripts\python.exe"
set "BRANCH=v2.1-accuracy-engine"

echo.
echo ==========================================
echo       NiftyAgent - Update and Start
echo ==========================================
echo.

REM Verify this is a Git checkout.
git -C "%REPO_DIR%" rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git repository not found at:
    echo %REPO_DIR%
    echo Please place this BAT in the repository root.
    pause
    exit /b 1
)

REM Do not overwrite tracked local edits.
git -C "%REPO_DIR%" diff --quiet
if errorlevel 1 (
    echo ERROR: You have uncommitted changes to tracked files.
    echo Commit or back up those changes, then run this BAT again.
    pause
    exit /b 1
)
git -C "%REPO_DIR%" diff --cached --quiet
if errorlevel 1 (
    echo ERROR: You have staged changes.
    echo Commit or unstage those changes, then run this BAT again.
    pause
    exit /b 1
)

echo [1/4] Switching to %BRANCH%...
git -C "%REPO_DIR%" switch "%BRANCH%"
if errorlevel 1 (
    echo ERROR: Could not switch to %BRANCH%.
    pause
    exit /b 1
)

echo.
echo [2/4] Pulling latest changes from GitHub...
git -C "%REPO_DIR%" pull --ff-only origin "%BRANCH%"
if errorlevel 1 (
    echo ERROR: Git pull failed. Resolve the Git issue before starting.
    pause
    exit /b 1
)

if not exist "%APP_DIR%\requirements.txt" (
    echo ERROR: App folder or requirements.txt not found:
    echo %APP_DIR%
    pause
    exit /b 1
)

if not exist "%PYTHON%" (
    echo ERROR: Existing virtual environment was not found:
    echo %PYTHON%
    echo Expected .venv in the repository root.
    pause
    exit /b 1
)

if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"

echo.
echo [3/4] Installing/updating Python dependencies...
"%PYTHON%" -m pip install -r "%APP_DIR%\requirements.txt"
if errorlevel 1 (
    echo ERROR: Dependency installation failed. App was not started.
    pause
    exit /b 1
)

echo.
echo [4/4] Starting worker, API, and dashboard...
start "NiftyAgent Live Worker" /min cmd /c "cd /d ""%APP_DIR%"" && ""%PYTHON%"" main.py >> logs\main.log 2>&1"
timeout /t 5 /nobreak >nul
start "NiftyAgent API" /min cmd /c "cd /d ""%APP_DIR%"" && ""%PYTHON%"" -m uvicorn api:app --host 127.0.0.1 --port 8000 >> logs\api.log 2>&1"
timeout /t 3 /nobreak >nul
start "NiftyAgent Streamlit UI" /min cmd /c "cd /d ""%APP_DIR%"" && ""%PYTHON%"" -m streamlit run app.py --server.headless true --server.port 8502 >> logs\streamlit.log 2>&1"

echo.
echo Startup commands have been launched.
echo Dashboard: http://localhost:8502
echo API health: http://127.0.0.1:8000/health
echo Worker log: %APP_DIR%\logs\main.log
echo API log:    %APP_DIR%\logs\api.log
echo UI log:     %APP_DIR%\logs\streamlit.log
echo.
echo Note: this starts the worker, but live market data still depends on
echo valid Kite credentials/session and the LIVE_MARKET_DATA setting in .env.
echo Automatic order placement remains disabled by configuration.
echo.
pause
