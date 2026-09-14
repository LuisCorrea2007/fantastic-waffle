@echo off
setlocal
cd /d %~dp0
if not exist .env copy .env.example .env >nul
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium firefox webkit
echo.
echo Instalacion completada.
echo Ejecuta start_windows.bat
pause
