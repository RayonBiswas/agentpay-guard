@echo off
rem One-click live check: sets up Python, then tests your Ollama model and the real PayPal sandbox.
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% --version >nul 2>nul || (echo Python not found. Install Python 3.11+ from python.org, tick "Add to PATH", then run this again. & pause & exit /b 1)
if not exist .venv (
  echo Setting up Python environment, first run only...
  %PY% -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)
if not exist .env copy .env.example .env >nul
python scripts\live_check.py
echo.
echo Copy everything above and paste it to Claude.
pause
