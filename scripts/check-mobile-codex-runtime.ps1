$workspace = Split-Path -Parent $PSScriptRoot
$upstream = if ($env:MOBILE_CODEX_UPSTREAM_DIR) {
  $env:MOBILE_CODEX_UPSTREAM_DIR
} else {
  Join-Path $workspace 'vendor\claudecodeui-1.25.2'
}

$nodeCommand = if ($env:MOBILE_CODEX_NODE) {
  Get-Item $env:MOBILE_CODEX_NODE -ErrorAction Stop
} else {
  Get-Command node -ErrorAction SilentlyContinue
}

$nginxCommand = if ($env:MOBILE_CODEX_NGINX) {
  Get-Item $env:MOBILE_CODEX_NGINX -ErrorAction Stop
} else {
  Get-Command nginx -ErrorAction SilentlyContinue
}

$tailscalePath = if ($env:MOBILE_CODEX_TAILSCALE) {
  $env:MOBILE_CODEX_TAILSCALE
} else {
  'C:\Program Files\Tailscale\tailscale.exe'
}

[PSCustomObject]@{
  Workspace = $workspace
  UpstreamExists = (Test-Path $upstream)
  UpstreamPath = $upstream
  Node = if ($nodeCommand) { $nodeCommand.Path } else { $null }
  Nginx = if ($nginxCommand) { $nginxCommand.Path } else { $null }
  Tailscale = if (Test-Path $tailscalePath) { $tailscalePath } else { $null }
  Python = (Get-Command python -ErrorAction SilentlyContinue).Path
} | Format-List
