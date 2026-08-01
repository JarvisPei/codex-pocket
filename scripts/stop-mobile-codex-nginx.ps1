$asciiAlias = if ($env:MOBILE_CODEX_ASCII_ALIAS) {
  $env:MOBILE_CODEX_ASCII_ALIAS
} else {
  Join-Path $env:SystemDrive 'mobileCodexHelper_ascii'
}

$pidFile = Join-Path $asciiAlias '.runtime\nginx\logs\mobile-codex.pid'
if (Test-Path $pidFile) {
  $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pidValue -match '^\d+$') {
    Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
  }
}

$listener = Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue
if ($listener) {
  $listener | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
  }
}
