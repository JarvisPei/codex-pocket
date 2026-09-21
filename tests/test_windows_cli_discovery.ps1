param([string]$CodexBinary)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\windows-cli-discovery.ps1')
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('pocket-cli-test-'+[Guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $testRoot
$script:checks=0
$script:probes=@()
$probe={param($path) $script:probes+=$path; return $true}
function Assert-Equal($actual,$expected,[string]$label){
    if($actual -ne $expected){throw "FAILED: $label ($actual != $expected)"}
    $script:checks++
}
function Fixture([string]$relative){
    $path=Join-Path $testRoot $relative
    $null=New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force
    [IO.File]::WriteAllBytes($path,[byte[]]@())
    return $path
}
try{
    $root=Join-Path $testRoot 'bin'
    $missing=Join-Path $root 'removed-version\codex.exe'
    $r=Resolve-PocketCli -SavedPath $missing -InstallRoot $root -Probe $probe
    Assert-Equal $r.status 'missing' 'no install'
    $new=Fixture 'bin\current-version\codex.exe'
    $r=Resolve-PocketCli -SavedPath $missing -InstallRoot $root -Probe $probe
    Assert-Equal $r.status 'ready' 'updated install recovered'
    Assert-Equal $r.path $new 'unique replacement selected'
    Assert-Equal $r.reason 'discovered' 'discovery indicated'
    $r=Resolve-PocketCli -InstallRoot $root -Probe $probe
    Assert-Equal $r.path $new 'first launch discovery'
    $second=Fixture 'bin\other-version\codex.exe'
    $before=$script:probes.Count
    $r=Resolve-PocketCli -SavedPath $missing -InstallRoot $root -Probe $probe
    Assert-Equal $r.status 'ambiguous' 'multiple installs require selection'
    Assert-Equal $script:probes.Count $before 'ambiguous binaries not executed'
    $r=Resolve-PocketCli -SavedPath $new -InstallRoot $root -Probe $probe
    Assert-Equal $r.path $new 'valid saved preference retained'
    $r=Resolve-PocketCli -ExplicitPath $second -SavedPath $new -InstallRoot $root -Probe $probe
    Assert-Equal $r.path $second 'explicit selection wins'
    $r=Resolve-PocketCli -ExplicitPath $missing -InstallRoot $root -Probe $probe
    Assert-Equal $r.reason 'explicit_failed' 'explicit invalid never silently replaced'
    $r=Resolve-PocketCli -SavedPath $new -InstallRoot $root -Probe {param($path) $false}
    Assert-Equal $r.reason 'saved_failed' 'failed saved probe never silently replaced'
    $r=Resolve-PocketCli -InstallRoot (Join-Path $root 'current-version') -Probe {param($path) $false}
    Assert-Equal $r.reason 'candidate_failed' 'invalid unique candidate rejected'
    Assert-Equal (Test-PocketCliPath (Fixture 'gui\ChatGPT.exe')) $false 'GUI rejected'
    Assert-Equal (Test-PocketCliPath (Fixture 'shims\codex.cmd')) $false 'shell shim rejected'
    Assert-Equal (Test-PocketCliPath 'relative\codex.exe') $false 'relative rejected'
    $nested=Fixture 'not-install\one\two\codex.exe'
    Assert-Equal @(Get-PocketCliCandidates (Join-Path $testRoot 'not-install')).Count 0 'no recursive scan'
    Assert-Equal (Invoke-PocketCliProbe $new) $false 'fake executable fails real probe'
    if($CodexBinary){Assert-Equal (Invoke-PocketCliProbe $CodexBinary) $true 'actual native CLI version verified'}
    @{ok=$true;checks=$script:checks}|ConvertTo-Json -Compress
}finally{
    # Only the uniquely named fixture directory created above, never user data.
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
