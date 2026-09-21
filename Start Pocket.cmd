@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0platforms\windows\scripts\windows-install-launcher.ps1" -StartPocket
if errorlevel 1 pause
