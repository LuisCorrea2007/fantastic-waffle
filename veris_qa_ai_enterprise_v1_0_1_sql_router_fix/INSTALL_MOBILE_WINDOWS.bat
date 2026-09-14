@echo off
setlocal
cd /d %~dp0
where node >nul 2>nul || (
  echo Node.js no esta instalado o no esta en PATH.
  echo Instala Node.js LTS y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)
call npm install -g appium
call appium driver install uiautomator2
call appium driver list --installed
echo.
echo Appium Android instalado. Inicia el servidor con: appium
pause
