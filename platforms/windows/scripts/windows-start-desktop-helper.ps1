# Explicit, on-demand desktop-session bootstrap. No trigger or autostart entry.
param(
    [Parameter(Mandatory=$true)][string]$PythonBinary,
    [switch]$EnableSmokeSend,
    [switch]$EnableNativeSend,
    [switch]$EnableNativeStop,
    [switch]$EnableNativeResume,
    [switch]$EnableTaskbarActivation
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
if($EnableTaskbarActivation -and -not $EnableNativeSend){throw 'Taskbar activation requires native send.'}
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$python = Get-Item -LiteralPath $PythonBinary
if ($python.PSIsContainer -or $python.Extension -ne '.exe' -or $python.FullName.Contains('"')) {
    throw 'Specify a native Python executable.'
}
$launcher = Join-Path $PSScriptRoot 'windows-desktop-helper.ps1'
if ($launcher.Contains('"')) { throw 'Unsupported launcher path.' }
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskName = 'CodexPocket-DesktopHelper-' + [Guid]::NewGuid().ToString('N')
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Minimized -File "' +
    $launcher + '" -PythonBinary "' + $python.FullName + '"'
if ($EnableSmokeSend) { $arguments += ' -EnableSmokeSend' }
if ($EnableNativeSend) { $arguments += ' -EnableNativeSend' }
if ($EnableNativeStop) { $arguments += ' -EnableNativeStop' }
if ($EnableNativeResume) { $arguments += ' -EnableNativeResume' }
if ($EnableTaskbarActivation) { $arguments += ' -EnableTaskbarActivation' }
$registered = $false
function Read-PocketHelperStatus {
    # Windows PowerShell 5.1 treats redirected native stderr as ErrorRecord.
    # A missing/stopped helper is normal on first startup, not a bootstrap error.
    $ErrorActionPreference = 'Continue'
    $text = (& $powershell -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File $launcher -PythonBinary $python.FullName -Action status 2>$null) -join ''
    if ($LASTEXITCODE -eq 0) {
        try { return ($text | ConvertFrom-Json -ErrorAction Stop) } catch { return $null }
    }
    return $null
}
try {
    # Interactive uses this user's existing signed-in token: no stored password,
    # SYSTEM account, elevation, automatic login, or credential prompt.
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
    $registered = $true
    # Stop only an authenticated Pocket helper, never Codex or a task in Codex.
    $old = Read-PocketHelperStatus
    if ($null -ne $old) {
        if ($old.reachable) {
            if ($old.status -ne 'ready') { throw 'Existing helper is busy; startup refused.' }
            & $powershell -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File $launcher -PythonBinary $python.FullName -Action stop | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Existing helper did not acknowledge stop.' }
            Start-Sleep -Milliseconds 500
        }
    }
    Start-ScheduledTask -TaskName $taskName
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $ready = $false
    do {
        Start-Sleep -Milliseconds 500
        $state = Read-PocketHelperStatus
        if ($null -ne $state) {
            if ($state.reachable -and $state.sessionId -gt 0 -and $state.instance -ne $old.instance -and
                [bool]$state.smokeSendAvailable -eq [bool]$EnableSmokeSend -and
                [bool]$state.nativeSendEnabled -eq [bool]$EnableNativeSend -and
                [bool]$state.nativeStopEnabled -eq [bool]$EnableNativeStop -and
                [bool]$state.nativeResumeEnabled -eq [bool]$EnableNativeResume -and
                [bool]$state.taskbarActivationEnabled -eq [bool]$EnableTaskbarActivation) {
                $ready=$true
                @{status='ready'; sessionId=$state.sessionId; smokeSendAvailable=$state.smokeSendAvailable; nativeSendEnabled=$state.nativeSendEnabled} | ConvertTo-Json -Compress
                break
            }
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $ready) { throw 'Desktop helper did not become ready. User must be signed in; no automatic login was attempted.' }
} finally {
    if ($registered) {
        # Removing registration does not terminate the already-running helper.
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}
