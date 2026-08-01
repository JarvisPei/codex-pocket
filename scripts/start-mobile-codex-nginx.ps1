$workspace = Split-Path -Parent $PSScriptRoot
$asciiAlias = if ($env:MOBILE_CODEX_ASCII_ALIAS) {
  $env:MOBILE_CODEX_ASCII_ALIAS
} else {
  Join-Path $env:SystemDrive 'mobileCodexHelper_ascii'
}

if (-not (Test-Path $asciiAlias)) {
  New-Item -ItemType Junction -Path $asciiAlias -Target $workspace | Out-Null
}

$nginxCmd = if ($env:MOBILE_CODEX_NGINX) {
  $env:MOBILE_CODEX_NGINX
} else {
  $found = Get-Command nginx -ErrorAction SilentlyContinue
  if (-not $found) {
    throw 'nginx not found on PATH. Set MOBILE_CODEX_NGINX if needed.'
  }
  $found.Path
}

$nginxRoot = Join-Path $asciiAlias '.runtime\nginx'
$confRoot = Join-Path $nginxRoot 'conf'
$logsRoot = Join-Path $nginxRoot 'logs'
$tempRoot = Join-Path $nginxRoot 'temp'
New-Item -ItemType Directory -Force -Path $confRoot | Out-Null
New-Item -ItemType Directory -Force -Path $logsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

Copy-Item -Force (Join-Path $workspace 'deploy\nginx-mobile-codex.conf') (Join-Path $confRoot 'mobile-codex-nginx.conf')
Copy-Item -Force (Join-Path $workspace 'deploy\nginx-mime.types') (Join-Path $confRoot 'mime.types')

Start-Process -FilePath $nginxCmd -ArgumentList @('-p', $nginxRoot, '-c', 'conf/mobile-codex-nginx.conf') -WindowStyle Hidden | Out-Null
