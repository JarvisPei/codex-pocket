$workspace = Split-Path -Parent $PSScriptRoot
$errors = @()

$blockedDirs = @('vendor', 'node_modules', 'dist', 'build', '.runtime', 'tmp', '__pycache__')
foreach ($dir in $blockedDirs) {
  if (Test-Path (Join-Path $workspace $dir)) {
    $errors += "Blocked directory present: $dir"
  }
}

$blockedFiles = Get-ChildItem -Path $workspace -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
  $_.FullName -notmatch '\\upstream-overrides\\' -and (
    $_.Extension -in '.db', '.log', '.sqlite', '.sqlite3', '.exe', '.pyc' -or
    $_.Name -eq '.env'
  )
}
foreach ($file in $blockedFiles) {
  $errors += "Blocked file present: $($file.FullName)"
}

$selfPath = Join-Path $workspace 'scripts\check-open-source-tree.ps1'
$textHits = Get-ChildItem -Path $workspace -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
  $_.FullName -ne $selfPath -and
  $_.Extension -in '.md', '.txt', '.ps1', '.cmd', '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.example', '.conf'
} | Select-String -Pattern 'jwt_secret|BEGIN PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY|BEGIN RSA PRIVATE KEY' -SimpleMatch
foreach ($hit in $textHits) {
  $errors += "Sensitive text pattern found in $($hit.Path):$($hit.LineNumber)"
}

if ($errors.Count -gt 0) {
  $errors | ForEach-Object { Write-Error $_ }
  exit 1
}

Write-Output 'Open-source tree check passed.'
