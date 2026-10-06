@echo off
setlocal

cd /d "C:\Users\Sagar\Downloads\nifty_intraday_agent_complete\nifty_intraday_agent_complete"

if not exist "..\.venv\Scripts\python.exe" (
    echo Python virtual environment not found.
    echo Expected: ..\.venv\Scripts\python.exe
    exit /b 1
)

if not exist "logs" mkdir logs

echo Starting Nifty Intraday Agent...

:LOOP
    echo [%date% %time%] Starting main.py
    start "" /b cmd /c "..\.venv\Scripts\python.exe main.py > logs\main.log 2>&1"
    timeout /t 10 /nobreak >nul

    echo [%date% %time%] Starting Streamlit dashboard on port 8502
    start "" /b cmd /c "..\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --server.port 8502 > logs\streamlit.log 2>&1"

    echo [%date% %time%] Waiting for 1 hour before restart check...
    timeout /t 3600 /nobreak >nul

    echo [%date% %time%] Restarting services to keep the app alive...
    taskkill /F /FI "WINDOWTITLE eq Nifty Live Worker*" /T >nul 2>&1
    taskkill /F /FI "WINDOWTITLE eq Nifty Streamlit UI*" /T >nul 2>&1

goto LOOP
