# Build a small GUI-subsystem bootstrap using Windows' installed .NET compiler.
# No downloads, elevation, startup registration, or script policy changes.
param([switch]$StartPocket, [switch]$FunctionsOnly)
$ErrorActionPreference='Stop'

function New-PocketGuiLauncher([string]$SourceFile, [string]$DestinationDirectory) {
    $compiler = @(
        (Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
        (Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $compiler) { throw 'Windows .NET compiler was not found. Pocket launcher was not installed.' }
    if (-not [IO.Path]::IsPathRooted($DestinationDirectory)) { throw 'Expected an absolute launcher directory.' }
    $null = New-Item -ItemType Directory -Path $DestinationDirectory -Force
    $directory = Get-Item -LiteralPath $DestinationDirectory
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Launcher directory must not be a junction or symlink.' }
    $output = Join-Path $directory.FullName 'CodexPocketLauncher.exe'
    if ((Test-Path -LiteralPath $output) -and ((Get-Item -LiteralPath $output).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Launcher file must not be a symlink.' }
    $temporary = Join-Path $directory.FullName ('build-' + [Guid]::NewGuid().ToString('N') + '.exe')
    try {
        $engine = [System.Management.Automation.PowerShell].Assembly.Location
        $compilerOutput = & $compiler /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll "/reference:$engine" "/out:$temporary" $SourceFile 2>&1
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temporary)) { throw 'GUI launcher compilation failed; the previous launcher was preserved.' }
        try { Move-Item -LiteralPath $temporary -Destination $output -Force }
        catch { throw 'Could not replace the launcher. Exit Pocket from its tray menu before updating; the previous launcher was preserved.' }
        return $output
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Test-PocketShortcutOwnership($Shortcut, [string]$Launcher, [string]$TrayScript) {
    if ($Shortcut.TargetPath -ieq $Launcher) { return $true }
    # Upgrade only the exact legacy Pocket shortcut. Preserve unrelated links.
    return ([IO.Path]::GetFileName($Shortcut.TargetPath) -ieq 'powershell.exe' -and
        $Shortcut.Arguments -eq ('-NoProfile -STA -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "' + $TrayScript + '"'))
}

if ($FunctionsOnly) { return }
if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run Pocket launcher setup as your regular user, not Administrator.'
}
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$trayScript = Join-Path $PSScriptRoot 'windows-pocket-tray.ps1'
$destination = Join-Path $env:LOCALAPPDATA 'CodexPocketLauncher'
$launcher = Join-Path $destination 'CodexPocketLauncher.exe'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex Pocket.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    if (-not (Test-PocketShortcutOwnership ($shell.CreateShortcut($shortcutPath)) $launcher $trayScript)) {
        throw 'An unrelated Codex Pocket shortcut already exists. It was not overwritten.'
    }
}
$launcher = New-PocketGuiLauncher (Join-Path $PSScriptRoot 'windows-pocket-launcher.cs') $destination
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.Arguments = '"' + $trayScript + '"'
$shortcut.WorkingDirectory = $source
$shortcut.Description = 'Codex Pocket - background tray, no terminal window'
$shortcut.Save()
Write-Output 'Codex Pocket desktop shortcut is ready. Daily startup needs only this shortcut; this setup terminal may be closed.'
if ($StartPocket) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $launcher
    $info.Arguments = '"' + $trayScript + '"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    [Diagnostics.Process]::Start($info).Dispose()
}
