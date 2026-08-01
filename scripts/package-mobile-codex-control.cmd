@echo off
setlocal
cd /d "%~dp0.."
py -3 -m pip install -r requirements.txt
pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name MobileCodexControl ^
  mobile_codex_control.py
