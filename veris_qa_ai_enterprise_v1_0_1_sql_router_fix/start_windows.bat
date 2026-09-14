@echo off
setlocal
cd /d %~dp0
if not exist .env copy .env.example .env >nul
call .venv\Scripts\activate
start "" http://127.0.0.1:8000
uvicorn app.main:app --host 127.0.0.1 --port 8000
