# Functions only. No PATH search, recursive disk scan, installation or task calls.
function Test-PocketCliPath([string]$Path) {
    if (-not $Path -or -not [IO.Path]::IsPathRooted($Path) -or $Path.Contains('"')) { return $false }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    return ($null -ne $item -and -not $item.PSIsContainer -and $item.Name -ieq 'codex.exe' -and
        -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint))
}

function Get-PocketCliCandidates([string]$InstallRoot) {
    $root = Get-Item -LiteralPath $InstallRoot -ErrorAction SilentlyContinue
    if (-not $root -or -not $root.PSIsContainer -or ($root.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return }
    $directories = @(Get-ChildItem -LiteralPath $root.FullName -Directory -ErrorAction Stop)
    if ($directories.Count -gt 64) { throw 'Too many Codex CLI directories; choose the current CLI manually.' }
    $paths = @((Join-Path $root.FullName 'codex.exe'))
    foreach ($directory in $directories) {
        if (-not ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $paths += Join-Path $directory.FullName 'codex.exe'
        }
    }
    $paths | Where-Object { Test-PocketCliPath $_ } | Sort-Object -Unique
}

function Invoke-PocketCliProbe([string]$Path) {
    if (-not (Test-PocketCliPath $Path)) { return $false }
    $process = $null
    try {
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $Path
        $info.Arguments = '--version'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $process = [Diagnostics.Process]::Start($info)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        if (-not $process.WaitForExit(5000)) {
            # Only the version probe we just created, never Desktop/another CLI.
            $process.Kill()
            $process.WaitForExit()
            return $false
        }
        return ($process.ExitCode -eq 0 -and $stdout.GetAwaiter().GetResult().Trim() -match '^codex-cli\s+\S+$')
    } catch { return $false } finally { if ($process) { $process.Dispose() } }
}

function Resolve-PocketCli {
    param([string]$SavedPath, [string]$ExplicitPath, [string]$InstallRoot,
          [scriptblock]$Probe = { param($path) Invoke-PocketCliProbe $path })
    # A path explicitly provided by the user must never silently select another.
    if ($ExplicitPath) {
        if ((Test-PocketCliPath $ExplicitPath) -and (& $Probe $ExplicitPath)) {
            return @{status='ready';path=$ExplicitPath;reason='explicit'}
        }
        return @{status='invalid';reason='explicit_failed'}
    }
    if (Test-PocketCliPath $SavedPath) {
        if (& $Probe $SavedPath) { return @{status='ready';path=$SavedPath;reason='saved'} }
        return @{status='invalid';reason='saved_failed'}
    }
    try { $candidates = @(Get-PocketCliCandidates $InstallRoot) }
    catch { return @{status='unavailable';reason='discovery_failed'} }
    if ($candidates.Count -eq 0) { return @{status='missing';reason='no_candidate'} }
    if ($candidates.Count -gt 1) { return @{status='ambiguous';reason='multiple_candidates';count=$candidates.Count} }
    if (& $Probe $candidates[0]) {
        return @{status='ready';path=$candidates[0];reason='discovered'}
    }
    return @{status='invalid';reason='candidate_failed'}
}
