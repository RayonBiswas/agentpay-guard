@echo off
rem Starts AgentPay Guard with your .env settings (PayPal sandbox + your Ollama model) on a clean demo state.
cd /d "%~dp0"
if not exist .venv (echo Run run_check.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python scripts\demo_reset.py
start "" http://localhost:8000
python -m uvicorn app.main:app --port 8000
pause
