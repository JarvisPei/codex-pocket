$workspace = Split-Path -Parent $PSScriptRoot

Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $workspace 'scripts\start-mobile-codex.ps1')) -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 5
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $workspace 'scripts\start-mobile-codex-nginx.ps1') | Out-Null
