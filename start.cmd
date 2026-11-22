@echo off
rem Uruchamia system COI (start.ps1) - dziala tez dwuklikiem. Omija blokade skryptow PowerShell
rem (domyslna polityka Restricted). Parametry jak w start.ps1: -Stop, -Token, -Logs, -NoBrowser.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
