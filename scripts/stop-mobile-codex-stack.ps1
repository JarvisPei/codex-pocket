$workspace = Split-Path -Parent $PSScriptRoot
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $workspace 'scripts\stop-mobile-codex-nginx.ps1') | Out-Null

$ports = @(3001, 8080)
foreach ($port in $ports) {
  $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
  if ($listener) {
    $listener | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
      Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
  }
}
