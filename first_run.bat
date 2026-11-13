@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title PetPal - Local Setup
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1"
set "PETPAL_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %PETPAL_RESULT%
