@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat goto novenv
call .venv\Scripts\activate
python seed.py
pause
exit /b
:novenv
echo Sperva zapusti start.bat odin raz, potom zakroi ego i zapusti seed.bat
pause
