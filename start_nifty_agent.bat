@echo off
setlocal

cd /d "C:\Users\Sagar\Downloads\nifty_intraday_agent_complete\nifty_intraday_agent_complete"

if not exist "..\.venv\Scripts\python.exe" (
    echo Python virtual environment not found.
    echo Expected: ..\.venv\Scripts\python.exe
    exit /b 1
)

if not exist "logs" mkdir logs

start "" /b cmd /c "..\.venv\Scripts\python.exe main.py > logs\main.log 2>&1"

timeout /t 10 /nobreak >nul

start "" /b cmd /c "..\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --server.port 8502 > logs\streamlit.log 2>&1"

echo Nifty Intraday Agent startup triggered.
echo Live worker log: logs\main.log
echo Streamlit UI: http://localhost:8502
exit /b 0
