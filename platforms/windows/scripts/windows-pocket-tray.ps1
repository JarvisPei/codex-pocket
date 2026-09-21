# Regular-user, on-demand tray entry. No scheduled task, autostart or elevation.
param([string]$PythonBinary, [string]$CodexBinary, [switch]$EnableTaskbarActivation)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$mutex = [Threading.Mutex]::new($false, ('Local\CodexPocket-Tray-' + $sid))
$ownsMutex = $false
$script:worker = $null
$script:tray = $null
$script:timer = $null
$script:reader = $null
$script:context = $null
$script:stopping = $false
$script:ready = $false
$script:lastState = 'Starting Pocket...'
$configPath = Join-Path $env:LOCALAPPDATA 'CodexPocket\launcher.json'
function Show-PocketMessage([string]$message) {
    [Windows.Forms.MessageBox]::Show($message, 'Codex Pocket') | Out-Null
}
function Pick-PocketExecutable([string]$title, [string]$name) {
    $dialog = [Windows.Forms.OpenFileDialog]::new()
    try {
        $dialog.Title = $title
        $dialog.Filter = "$name|$name"
        $dialog.CheckFileExists = $true
        $dialog.RestoreDirectory = $true
        if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { throw [OperationCanceledException]::new('File selection cancelled.') }
        return $dialog.FileName
    } finally { $dialog.Dispose() }
}
function Validate-PocketExecutable([string]$path, [string]$name) {
    if (-not [IO.Path]::IsPathRooted($path) -or $path.Contains('"')) { throw "Select an absolute $name path." }
    $file = Get-Item -LiteralPath $path
    if ($file.PSIsContainer -or $file.Name -ine $name) { throw "Select $name, not a shortcut or GUI application." }
    return $file.FullName
}
try {
    . (Join-Path $PSScriptRoot 'windows-cli-discovery.ps1')
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { Show-PocketMessage 'Pocket is already open. Look in the notification area (including hidden icons).'; return }
    if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run Pocket as your regular user, not Administrator.'
    }
    $saved = $null
    if (Test-Path -LiteralPath $configPath) {
        try { $saved = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json } catch {}
    }
    if (-not $PythonBinary -and $saved) { $PythonBinary = $saved.pythonBinary }
    $explicitCli = $CodexBinary
    if ($saved -and -not $PSBoundParameters.ContainsKey('EnableTaskbarActivation')) { $EnableTaskbarActivation = [bool]$saved.taskbarActivation }
    if (-not $PythonBinary -or -not (Test-Path -LiteralPath $PythonBinary -PathType Leaf)) {
        $PythonBinary = Pick-PocketExecutable 'Select installed native Python 3.11+ (python.exe)' 'python.exe'
    }
    $PythonBinary = Validate-PocketExecutable $PythonBinary 'python.exe'
    $savedCli = if ($saved) { $saved.codexBinary } else { $null }
    $cli = Resolve-PocketCli -SavedPath $savedCli -ExplicitPath $explicitCli -InstallRoot (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin')
    if ($cli.status -eq 'ready') {
        $CodexBinary = $cli.path
    } else {
        $explanation = switch ($cli.status) {
            'ambiguous' { 'More than one installed Codex CLI was found. Pocket will not guess which version to use.' }
            'missing' { 'The saved Codex CLI is missing (possibly after an update), and no replacement was found in the standard install folder.' }
            'invalid' { 'The selected/saved Codex CLI did not pass its version check. Pocket has not started.' }
            default { 'Pocket could not inspect the Codex CLI install folder.' }
        }
        Show-PocketMessage ($explanation + "`n`nSelect the current codex.exe next (not ChatGPT.exe). Cancel exits Pocket without changing pairing or the saved configuration.")
        $CodexBinary = Pick-PocketExecutable 'Select current Codex CLI (codex.exe, not ChatGPT.exe)' 'codex.exe'
        if (-not (Invoke-PocketCliProbe $CodexBinary)) { throw 'The selected codex.exe failed its version check. Check the current Codex installation and try again. Pairing has not been changed.' }
    }
    $CodexBinary = Validate-PocketExecutable $CodexBinary 'codex.exe'
    if ($source.Contains('"')) { throw 'Unsupported source path.' }
    # Refuse any pre-existing listener. Never kill/adopt another preview process.
    if (Get-NetTCPConnection -State Listen -LocalPort 4317 -ErrorAction SilentlyContinue) {
        throw 'Port 4317 is already in use. Close your previous Pocket preview when idle, then reopen this launcher. No process was stopped.'
    }
    $script:context = [Windows.Forms.ApplicationContext]::new()
    $script:tray = [Windows.Forms.NotifyIcon]::new()
    $script:tray.Icon = [Drawing.SystemIcons]::Application
    $script:tray.Text = 'Codex Pocket - starting'
    $menu = [Windows.Forms.ContextMenuStrip]::new()
    $statusItem = $menu.Items.Add('Starting Pocket...')
    $statusItem.Enabled = $false
    $pairItem = $menu.Items.Add('Pair phone / QR code')
    $pairItem.Enabled = $false
    $pairItem.add_Click({
        # Separate bounded pairing UI uses existing DPAPI credential; no ticket in arguments.
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $PythonBinary
        $info.Arguments = '"' + (Join-Path $source 'scripts\pair-device.py') + '" --launch'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        [Diagnostics.Process]::Start($info).Dispose()
    })
    $exitItem = $menu.Items.Add('Exit Pocket (keep Codex running)')
    $exitItem.add_Click({
        if (-not $script:stopping) {
            $answer = [Windows.Forms.MessageBox]::Show('Disconnect phone access and exit Pocket? Codex stays open and its running tasks are not stopped.', 'Codex Pocket', 'YesNo', 'Question')
            if ($answer -eq [Windows.Forms.DialogResult]::Yes) {
                $script:stopping = $true
                $pairItem.Enabled = $false
                $script:lastState = 'Finishing pending requests...'
                $statusItem.Text = $script:lastState
                $script:worker.StandardInput.WriteLine('stop')
                $script:worker.StandardInput.Flush()
            }
        }
    })
    $script:tray.ContextMenuStrip = $menu
    $script:tray.add_DoubleClick({ Show-PocketMessage $script:lastState })
    $script:tray.Visible = $true
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $PythonBinary
    # Explicit sys.path supports the portable isolated Python used by testers.
    $bootstrap = 'import sys; sys.path.insert(0, sys.argv[1]); from platforms.windows.pocket import main; raise SystemExit(main(sys.argv[2:]))'
    $info.Arguments = '-u -c "' + $bootstrap + '" "' + $source + '" --codex-binary "' + $CodexBinary + '"'
    if ($EnableTaskbarActivation) { $info.Arguments += ' --taskbar-activation' }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $script:worker = [Diagnostics.Process]::Start($info)
    # Drain asynchronously; do not log message contents, file paths or secrets.
    $discardDiagnostics = $script:worker.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
    $script:reader = $script:worker.StandardOutput.ReadLineAsync()
    $script:timer = [Windows.Forms.Timer]::new()
    $script:timer.Interval = 300
    $script:timer.add_Tick({
        try {
            while ($script:reader -and $script:reader.IsCompleted) {
                $line = $script:reader.GetAwaiter().GetResult()
                $script:reader = $null
                if ($null -eq $line) { break }
                $state = $line | ConvertFrom-Json
                switch ($state.status) {
                    'ready' {
                        $script:ready = $true
                        $script:lastState = 'Pocket running - desktop helper and phone service ready'
                        $script:tray.Text = 'Codex Pocket - running'
                        $pairItem.Enabled = $true
                        @{pythonBinary=$PythonBinary;codexBinary=$CodexBinary;taskbarActivation=[bool]$EnableTaskbarActivation} |
                            ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
                        $script:tray.ShowBalloonTip(4000, 'Codex Pocket', 'Running in the notification area. No terminal needs to stay open.', [Windows.Forms.ToolTipIcon]::Info)
                    }
                    'stopping' { $script:lastState = 'Finishing pending requests...' }
                    'error' { $script:lastState = 'Pocket unavailable: ' + $state.error + '. ' + $state.hint }
                    'stopped' { $script:lastState = 'Pocket stopped' }
                }
                $statusItem.Text = $script:lastState
                $script:reader = $script:worker.StandardOutput.ReadLineAsync()
            }
            if ($script:worker.HasExited) {
                $script:timer.Stop()
                if (-not $script:stopping) {
                    if ($script:lastState -eq 'Starting Pocket...') { $script:lastState = 'Pocket could not start its Python runtime. Check Python 3.11+ and the source files, then reopen Pocket. Pairing was not reset.' }
                    Show-PocketMessage $script:lastState
                }
                $script:context.ExitThread()
            }
        } catch {
            $script:timer.Stop()
            Show-PocketMessage 'Pocket launcher failed. Close it and check your Python/CLI paths. No Codex task was stopped.'
            $script:context.ExitThread()
        }
    })
    $script:timer.Start()
    [Windows.Forms.Application]::Run($script:context)
} catch [OperationCanceledException] {
    # Closing the picker is a normal exit, not a second modal error dialog.
} catch {
    Show-PocketMessage $_.Exception.Message
} finally {
    if ($script:timer) { $script:timer.Dispose() }
    if ($script:worker) {
        # EOF requests cooperative shutdown, even if the tray failed. Never Kill.
        try { $script:worker.StandardInput.Close() } catch {}
        $script:worker.Dispose()
    }
    if ($script:tray) { $script:tray.Visible = $false; $script:tray.Dispose() }
    if ($script:context) { $script:context.Dispose() }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
