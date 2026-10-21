@echo off
setlocal EnableExtensions

REM Run this file from anywhere; resolve paths relative to the repository.
set "REPO_DIR=%~dp0"
set "APP_DIR=%REPO_DIR%nifty_intraday_agent_complete"

if not exist "%APP_DIR%\main.py" (
    echo ERROR: Could not find "%APP_DIR%\main.py".
    echo Place this batch file in the NiftyAgent repository root.
    pause
    exit /b 1
)

REM Support either the repository-level or app-level virtual environment.
if exist "%REPO_DIR%.venv\Scripts\python.exe" (
    set "PYTHON=%REPO_DIR%.venv\Scripts\python.exe"
) else if exist "%APP_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON=%APP_DIR%\.venv\Scripts\python.exe"
) else (
    echo ERROR: Python virtual environment not found.
    echo Expected either:
    echo   "%REPO_DIR%.venv\Scripts\python.exe"
    echo   "%APP_DIR%\.venv\Scripts\python.exe"
    pause
    exit /b 1
)

cd /d "%APP_DIR%"
if not exist "logs" mkdir "logs"
if not exist "data" mkdir "data"

if not exist ".env" (
    echo WARNING: .env was not found in "%APP_DIR%".
    echo Copy .env.example to .env and configure Kite credentials before live data can connect.
)

echo Starting NiftyAgent from:
echo   %APP_DIR%
echo Python:
echo   %PYTHON%
echo.

REM Start the API first because Kite login and token callbacks use it.
start "NiftyAgent API" /min cmd /c ""%PYTHON%" -m uvicorn api:app --host 127.0.0.1 --port 8000 > logs\api.log 2>&1"
timeout /t 3 /nobreak >nul

REM Start the live worker.
start "NiftyAgent Worker" /min cmd /c ""%PYTHON%" -u main.py > logs\main.log 2>&1"
timeout /t 3 /nobreak >nul

REM Start the dashboard.
start "NiftyAgent Dashboard" /min cmd /c ""%PYTHON%" -m streamlit run app.py --server.headless true --server.port 8502 > logs\streamlit.log 2>&1"

echo.
echo Startup commands have been issued.
echo Dashboard:  http://localhost:8502
echo API health: http://127.0.0.1:8000/health
echo Kite login: http://127.0.0.1:8000/kite/login
echo Logs:
echo   %APP_DIR%\logs\main.log
echo   %APP_DIR%\logs\api.log
echo   %APP_DIR%\logs\streamlit.log
echo.
echo If the worker does not connect, check main.log for the first ERROR.
pause
