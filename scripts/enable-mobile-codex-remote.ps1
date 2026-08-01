$tailscale = if ($env:MOBILE_CODEX_TAILSCALE) {
  $env:MOBILE_CODEX_TAILSCALE
} else {
  'C:\Program Files\Tailscale\tailscale.exe'
}

if (-not (Test-Path $tailscale)) {
  throw "Tailscale CLI not found: $tailscale"
}

$status = & $tailscale status --json | ConvertFrom-Json
if ($status.BackendState -ne 'Running') {
  if ($status.AuthURL) {
    Write-Output "Tailscale login required: $($status.AuthURL)"
    exit 1
  }

  throw 'Tailscale is not running yet.'
}

$serveOutput = & $tailscale serve --bg http://127.0.0.1:8080 2>&1
$serveText = ($serveOutput | Out-String).Trim()

if ($serveText -match 'https://login\.tailscale\.com/f/serve\?[^\s]+') {
  Write-Output "Tailscale Serve must be enabled on your tailnet first: $($Matches[0])"
  exit 1
}

if ($LASTEXITCODE -ne 0) {
  if ($serveText) {
    throw $serveText
  }

  throw 'Failed to enable Tailscale Serve.'
}

& $tailscale serve status
