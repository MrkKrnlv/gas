@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=py
where py >nul 2>&1 || set PY=python
%PY% --version >nul 2>&1 || (echo Python не найден. Установи с python.org и поставь галочку "Add Python to PATH". & pause & exit /b 1)
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if errorlevel 1 (echo Ошибка установки зависимостей & pause & exit /b 1)
start "" http://localhost:8000
python -m uvicorn main:app --port 8000
echo.
echo Сервер остановился, смотри ошибку выше.
pause
