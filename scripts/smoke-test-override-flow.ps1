param(
  [string]$UpstreamZip = '',
  [string]$ScratchRoot = $(Join-Path $env:TEMP ("mobileCodexHelper-smoke-" + (Get-Date -Format 'yyyyMMdd-HHmmss')))
)

$ErrorActionPreference = 'Stop'

$workspace = Split-Path -Parent $PSScriptRoot
$overrideRoot = Join-Path $workspace 'upstream-overrides\claudecodeui-1.25.2'
$applyScript = Join-Path $PSScriptRoot 'apply-upstream-overrides.ps1'

if (-not $UpstreamZip) {
  $zipCandidates = @(
    (Join-Path $workspace 'vendor\claudecodeui-v1.25.2.zip'),
    (Join-Path $workspace 'vendor\claudecodeui-1.25.2.zip'),
    (Join-Path $workspace 'claudecodeui-v1.25.2.zip'),
    (Join-Path $workspace 'claudecodeui-1.25.2.zip')
  )
  $UpstreamZip = $zipCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not (Test-Path $UpstreamZip)) {
  throw "Upstream zip not found. Pass -UpstreamZip <path-to-zip> or place the zip in vendor\\."
}

if (-not (Test-Path $overrideRoot)) {
  throw "Override root not found: $overrideRoot"
}

New-Item -ItemType Directory -Force -Path $ScratchRoot | Out-Null
Expand-Archive -LiteralPath $UpstreamZip -DestinationPath $ScratchRoot -Force

$candidates = Get-ChildItem -Path $ScratchRoot -Directory | Where-Object {
  Test-Path (Join-Path $_.FullName 'package.json')
}

if ($candidates.Count -ne 1) {
  throw "Expected exactly one extracted upstream directory under $ScratchRoot, found $($candidates.Count)."
}

$targetRoot = $candidates[0].FullName
$env:MOBILE_CODEX_UPSTREAM_DIR = $targetRoot

try {
  powershell -NoProfile -ExecutionPolicy Bypass -File $applyScript | Write-Output

  $missing = @()
  Get-ChildItem -Path $overrideRoot -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($overrideRoot.Length + 1)
    $destination = Join-Path $targetRoot $relative
    if (-not (Test-Path $destination)) {
      $missing += $relative
    }
  }

  if ($missing.Count -gt 0) {
    $missing | ForEach-Object { Write-Error "Missing override target: $_" }
    exit 1
  }

  Write-Output ''
  Write-Output 'Smoke test passed.'
  Write-Output "ScratchRoot = $ScratchRoot"
  Write-Output "TargetRoot  = $targetRoot"
} finally {
  Remove-Item Env:\MOBILE_CODEX_UPSTREAM_DIR -ErrorAction SilentlyContinue
}
