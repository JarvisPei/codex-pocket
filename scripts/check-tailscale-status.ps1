$tailscale = if ($env:MOBILE_CODEX_TAILSCALE) {
  $env:MOBILE_CODEX_TAILSCALE
} else {
  'C:\Program Files\Tailscale\tailscale.exe'
}

if (-not (Test-Path $tailscale)) {
  throw "Tailscale CLI not found: $tailscale"
}

$status = & $tailscale status --json | ConvertFrom-Json

[PSCustomObject]@{
  BackendState = $status.BackendState
  LoggedIn = ($status.BackendState -eq 'Running')
  AuthURL = $status.AuthURL
  HostName = $status.Self.HostName
  DNSName = $status.Self.DNSName
  Health = ($status.Health -join '; ')
} | Format-List
