# On-demand, time-limited phone preview. No startup trigger or stored password.
param(
    [Parameter(Mandatory=$true)][string]$PythonBinary,
    [Parameter(Mandatory=$true)][string]$CodexBinary,
    [ValidateRange(1,120)][int]$Minutes=120,
    [switch]$EnableNativeNewTasks,
    [switch]$EnableAttachmentPaths,
    [switch]$RunWorker
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
foreach($binary in @($PythonBinary,$CodexBinary)) {
    $item=Get-Item -LiteralPath $binary
    if($item.PSIsContainer -or $item.Extension -ne '.exe' -or $item.FullName.Contains('"')) { throw 'Expected native executable path.' }
}
$PythonBinary=(Get-Item -LiteralPath $PythonBinary).FullName
$CodexBinary=(Get-Item -LiteralPath $CodexBinary).FullName
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
if($RunWorker) {
    # Fixed Python code, validated paths passed as arguments rather than code.
    $code=@'
import sys,threading
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from platforms.windows.bridge import make_server,windows_token,open_first_install_pairing
server=make_server(Path(sys.argv[2]),windows_token(),4317,native_text_send=True,native_new_tasks=sys.argv[4]=='1',attachment_paths=sys.argv[5]=='1')
open_first_install_pairing(4317)
timer=threading.Timer(int(sys.argv[3])*60,server.shutdown)
timer.daemon=True
timer.start()
try:
    server.serve_forever()
finally:
    server.server_close()
    server.app_server.close()
'@
    $createFlag=if($EnableNativeNewTasks){'1'}else{'0'}
    $attachmentFlag=if($EnableAttachmentPaths){'1'}else{'0'}
    & $PythonBinary -c $code $source $CodexBinary $Minutes $createFlag $attachmentFlag
    exit $LASTEXITCODE
}
if(Get-NetTCPConnection -State Listen -LocalPort 4317 -ErrorAction SilentlyContinue) { throw 'Port 4317 already occupied; refusing to replace another server.' }
$launcher=$PSCommandPath
if($launcher.Contains('"')) { throw 'Unsupported script path.' }
$powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskName='CodexPocket-PhonePreview-'+[Guid]::NewGuid().ToString('N')
$args='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "'+$launcher+'" -PythonBinary "'+$PythonBinary+'" -CodexBinary "'+$CodexBinary+'" -Minutes '+$Minutes+' -RunWorker'
if($EnableNativeNewTasks){$args+=' -EnableNativeNewTasks'}
if($EnableAttachmentPaths){$args+=' -EnableAttachmentPaths'}
$registered=$false
try {
    $principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $action=New-ScheduledTaskAction -Execute $powershell -Argument $args
    $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes ($Minutes+1))
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
    $registered=$true
    Start-ScheduledTask -TaskName $taskName
    $deadline=[DateTime]::UtcNow.AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $health=Invoke-RestMethod -Uri 'http://127.0.0.1:4317/health' -TimeoutSec 2 -Proxy $null
            if($health.ok -and $health.service -eq 'codex-pocket-bridge' -and $health.capabilities.nativeTextSend -eq $true) {
                @{status='ready';port=4317;durationMinutes=$Minutes;mode='native-text-preview'} | ConvertTo-Json -Compress
                return
            }
        } catch {}
    } while([DateTime]::UtcNow -lt $deadline)
    # Only this launcher's temporary job, never Desktop or a pre-existing bridge.
    Stop-ScheduledTask -TaskName $taskName
    throw 'Preview did not become ready; temporary job stopped.'
} finally {
    if($registered) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
}
