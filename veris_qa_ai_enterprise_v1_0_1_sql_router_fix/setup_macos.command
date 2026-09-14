#!/bin/bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium firefox webkit
echo "Instalación completada. Ejecuta start_macos.command"
