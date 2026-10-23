@echo off
setlocal EnableExtensions

REM Run this file from anywhere; the app folder is the folder containing this BAT.
set "APP_DIR=%~dp0"
set "PYTHON=%APP_DIR%..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%APP_DIR%.venv\Scripts\python.exe"

echo.
echo ==========================================
echo          NiftyAgent - Start
echo ==========================================
echo.

if not exist "%APP_DIR%app.py" (
    echo ERROR: App folder or requirements.txt not found:
    echo %APP_DIR%
    pause
    exit /b 1
)

if not exist "%PYTHON%" (
    echo ERROR: Existing virtual environment was not found:
    echo %PYTHON%
    echo Expected .venv in this folder or its parent folder.
    pause
    exit /b 1
)

if not exist "%APP_DIR%logs" mkdir "%APP_DIR%logs"
cd /d "%APP_DIR%"

echo.
echo Starting dashboard on http://127.0.0.1:8502...
powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8502/_stcore/health).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
if errorlevel 1 (
    start "NiftyAgent Streamlit UI" /min cmd /c ""%PYTHON%" -m streamlit run "%APP_DIR%app.py" --server.headless true --server.port 8502 >> logs\streamlit.log 2>&1"
    for /L %%I in (1,1,30) do (
        powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8502/_stcore/health).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
        if not errorlevel 1 goto UI_READY
        timeout /t 1 /nobreak >nul
    )
    echo ERROR: Dashboard did not become ready. Check:
    echo %APP_DIR%logs\streamlit.log
    pause
    exit /b 1
)

:UI_READY
start "" "http://127.0.0.1:8502"

echo.
echo Checking live worker...
powershell -NoProfile -Command "if (Get-WmiObject Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*%APP_DIR%main.py*' }) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    start "NiftyAgent Live Worker" /min cmd /c ""%PYTHON%" "%APP_DIR%main.py" >> logs\main.log 2>&1"
) else (
    echo Live worker is already running; skipping duplicate start.
)

echo Checking API...
powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/health).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
if errorlevel 1 (
    start "NiftyAgent API" /min cmd /c ""%PYTHON%" -m uvicorn api:app --app-dir "%APP_DIR%" --host 127.0.0.1 --port 8000 >> logs\api.log 2>&1"
    for /L %%I in (1,1,30) do (
        powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/health).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
        if not errorlevel 1 goto API_READY
        timeout /t 1 /nobreak >nul
    )
    echo WARNING: API did not become ready. Check:
    echo %APP_DIR%logs\api.log
) else (
    echo API is already healthy; skipping duplicate start.
)

:API_READY

echo.
echo Dashboard is ready.
echo Dashboard: http://localhost:8502
echo API health: http://127.0.0.1:8000/health
echo Worker log: %APP_DIR%logs\main.log
echo API log:    %APP_DIR%logs\api.log
echo UI log:     %APP_DIR%logs\streamlit.log
echo.
pause
