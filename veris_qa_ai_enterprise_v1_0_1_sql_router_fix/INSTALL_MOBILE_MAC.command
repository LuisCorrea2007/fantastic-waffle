#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js no esta instalado. Instala Node.js LTS y vuelve a ejecutar."
  exit 1
fi
npm install -g appium
appium driver install uiautomator2 || true
if command -v xcodebuild >/dev/null 2>&1; then
  appium driver install xcuitest || true
fi
appium driver list --installed
printf '\nAppium instalado. Inicia el servidor con: appium\n'
