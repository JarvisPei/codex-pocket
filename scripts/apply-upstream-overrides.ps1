$workspace = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $workspace 'upstream-overrides\claudecodeui-1.25.2'
$targetRoot = if ($env:MOBILE_CODEX_UPSTREAM_DIR) {
  $env:MOBILE_CODEX_UPSTREAM_DIR
} else {
  Join-Path $workspace 'vendor\claudecodeui-1.25.2'
}

if (-not (Test-Path $sourceRoot)) {
  throw "Override source not found: $sourceRoot"
}

if (-not (Test-Path $targetRoot)) {
  throw "Upstream checkout not found: $targetRoot"
}

$copied = 0
Get-ChildItem -Path $sourceRoot -Recurse -File | ForEach-Object {
  $relative = $_.FullName.Substring($sourceRoot.Length + 1)
  $destination = Join-Path $targetRoot $relative
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
  Copy-Item -Force $_.FullName $destination
  $copied++
}

Write-Output "Applied $copied override files to $targetRoot"
