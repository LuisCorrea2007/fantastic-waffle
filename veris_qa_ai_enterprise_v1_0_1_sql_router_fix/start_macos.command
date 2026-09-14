#!/bin/bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
source .venv/bin/activate
open http://127.0.0.1:8000
uvicorn app.main:app --host 127.0.0.1 --port 8000
