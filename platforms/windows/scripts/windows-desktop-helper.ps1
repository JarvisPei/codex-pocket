param(
    [Parameter(Mandatory=$true)][string]$PythonBinary,
    [ValidateSet('serve', 'status', 'diagnose', 'stop', 'smoke-send')][string]$Action = 'serve',
    [switch]$EnableSmokeSend,
    [switch]$EnableNativeSend,
    [switch]$EnableNativeStop,
    [switch]$EnableNativeResume,
    [switch]$EnableTaskbarActivation
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$python = Get-Item -LiteralPath $PythonBinary
if ($python.PSIsContainer -or $python.Extension -ne '.exe') { throw 'Specify a native Python executable.' }
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
# Explicit sys.path also supports the isolated official embeddable Python runtime.
# Action is an allowlisted argv value, never interpolated Python/PowerShell code.
$arguments = @($Action)
if ($EnableSmokeSend) { $arguments += '--enable-smoke-send' }
if ($EnableNativeSend) { $arguments += '--enable-native-send' }
if ($EnableNativeStop) { $arguments += '--enable-native-stop' }
if ($EnableNativeResume) { $arguments += '--enable-native-resume' }
if ($EnableTaskbarActivation) { $arguments += '--enable-taskbar-activation' }
& $python.FullName -c "import sys; sys.path.insert(0, sys.argv[1]); from platforms.windows.desktop_helper import main; raise SystemExit(main(sys.argv[2:]))" $root @arguments
exit $LASTEXITCODE
