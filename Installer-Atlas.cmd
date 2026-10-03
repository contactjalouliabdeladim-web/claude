@echo off
rem Double-clic : installation guidee d Atlas (sans droits administrateur).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\Installer-Atlas.ps1" %*
pause
