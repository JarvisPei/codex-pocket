# Narrow Desktop coordination adapter: mark one task read, no task execution.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$pipe=$null
$stage='payload'
try {
    $text=[Console]::In.ReadToEnd()
    if($text.Length -gt 4096){throw 'Invalid payload'}
    $payload=$text | ConvertFrom-Json
    if($payload.threadId -notmatch '^[0-9a-fA-F-]{36}$' -or $null -eq $payload.context.identity -or
       $payload.context.executionHostKey -ne 'local:092af2cb59bdd804c6f7f1cd1d85464b682974e43cd517397d25510024034d1c'){throw 'Invalid context'}
    Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class PocketReadPipe {
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool GetNamedPipeServerProcessId(IntPtr h, out uint pid);
}
'@
    $pipe=[IO.Pipes.NamedPipeClientStream]::new('.', 'codex-ipc', [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous, [Security.Principal.TokenImpersonationLevel]::Identification)
    $stage='connect'
    $pipe.Connect(1500)
    [uint32]$serverPid=0
    if(-not [PocketReadPipe]::GetNamedPipeServerProcessId($pipe.SafePipeHandle.DangerousGetHandle(),[ref]$serverPid)){throw 'Unknown peer'}
    $stage='peer'
    $peer=Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid"
    $owner=Invoke-CimMethod -InputObject $peer -MethodName GetOwnerSid
    if($owner.Sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value){throw 'Wrong peer owner'}
    $trusted=@(Get-AppxPackage -Name OpenAI.Codex | ForEach-Object {Join-Path $_.InstallLocation 'app\ChatGPT.exe'})
    if(-not $peer.ExecutablePath -or $trusted -notcontains $peer.ExecutablePath){throw 'Unknown Desktop executable'}
    function Write-Frame($value) {
        $bytes=[Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Compress -Depth 12))
        if($bytes.Length -gt 8192){throw 'Oversized request'}
        $frame=New-Object byte[] (4+$bytes.Length)
        [BitConverter]::GetBytes([uint32]$bytes.Length).CopyTo($frame,0)
        $bytes.CopyTo($frame,4)
        $task=$pipe.WriteAsync($frame,0,$frame.Length)
        if(-not $task.Wait(1500)){throw 'Write timeout'}
        $null=$task.GetAwaiter().GetResult()
    }
    function Read-Bytes([int]$length) {
        $bytes=New-Object byte[] $length
        $offset=0
        while($offset -lt $length){
            $task=$pipe.ReadAsync($bytes,$offset,$length-$offset)
            if(-not $task.Wait(1500)){throw 'Read timeout'}
            $count=$task.GetAwaiter().GetResult()
            if($count -le 0){throw 'Peer closed'}
            $offset+=$count
        }
        return ,$bytes
    }
    $stage='initialize'
    $requestId=[Guid]::NewGuid().ToString()
    Write-Frame @{type='request';requestId=$requestId;sourceClientId='';version=0;method='initialize';params=@{clientType='codex-pocket'}}
    $clientId=$null
    for($i=0;$i -lt 8;$i++){
        $length=[BitConverter]::ToUInt32((Read-Bytes 4),0)
        if($length -lt 1 -or $length -gt 1048576){throw 'Oversized reply'}
        $reply=[Text.Encoding]::UTF8.GetString((Read-Bytes $length)) | ConvertFrom-Json
        if($reply.type -eq 'response' -and $reply.requestId -eq $requestId){
            if($reply.resultType -ne 'success' -or $reply.method -ne 'initialize'){throw 'Initialize rejected'}
            $clientId=$reply.result.clientId
            break
        }
    }
    if($clientId -notmatch '^[0-9a-fA-F-]{36}$'){throw 'No client identity'}
    $stage='notify'
    Write-Frame @{type='broadcast';method='thread-read-state-changed';sourceClientId=$clientId;version=3;
        params=@{hostId='local';conversationId=$payload.threadId;hasUnreadTurn=$false;context=$payload.context}}
    # Sending is not success; Python separately verifies Desktop persistence.
    '{"sent":true}'
} catch {
    @{sent=$false;stage=$stage;line=$_.InvocationInfo.ScriptLineNumber} | ConvertTo-Json -Compress
    exit 1
} finally {if($null -ne $pipe){$pipe.Dispose()}}
