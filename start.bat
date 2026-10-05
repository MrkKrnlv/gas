@echo off
cd /d "%~dp0"
set PY=py
where py >nul 2>&1 || set PY=python
%PY% --version >nul 2>&1
if errorlevel 1 goto nopy
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if errorlevel 1 goto fail
start "" http://localhost:8000
python -m uvicorn main:app --port 8000
:fail
echo.
echo ERROR - see messages above
pause
exit /b 1
:nopy
echo Python is not installed. Download it from python.org and tick "Add Python to PATH" during install.
pause
exit /b 1
