@echo off
rem Double-clic : demarre Atlas et ouvre l interface dans le navigateur.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\Lancer-Atlas.ps1" %*
if errorlevel 1 pause
