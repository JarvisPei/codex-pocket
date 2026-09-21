$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'scripts\windows-install-launcher.ps1') -FunctionsOnly
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('pocket gui '+[char]0x6d4b+[char]0x8bd5+'-'+[Guid]::NewGuid().ToString('N'))
$checks=0
function Assert-True($value,[string]$message) {
    if(-not $value){throw "FAILED: $message"}
    $script:checks++
}
try {
    $exe=New-PocketGuiLauncher (Join-Path $root 'scripts\windows-pocket-launcher.cs') $testRoot
    $bytes=[IO.File]::ReadAllBytes($exe)
    $pe=[BitConverter]::ToInt32($bytes,0x3c)
    Assert-True ([BitConverter]::ToUInt16($bytes,$pe+24+68) -eq 2) 'PE subsystem must be Windows GUI (not Console)'
    $tray=Join-Path $root 'scripts\windows-pocket-tray.ps1'
    $legacy=[pscustomobject]@{TargetPath='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';Arguments='-NoProfile -STA -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "'+$tray+'"'}
    Assert-True (Test-PocketShortcutOwnership $legacy $exe $tray) 'exact legacy shortcut upgrades'
    $legacy.Arguments+=' -DifferentProgram'
    Assert-True (-not (Test-PocketShortcutOwnership $legacy $exe $tray)) 'unrelated shortcut preserved'
    Assert-True (Test-PocketShortcutOwnership ([pscustomobject]@{TargetPath=$exe;Arguments='unused'}) $exe $tray) 'owned GUI shortcut can update its source path'
    # A fake tray script observes the actual hosted engine. No Pocket API,
    # model, credentials, user shortcut, or live service is used by this test.
    $probe=Join-Path $testRoot 'windows-pocket-tray.ps1'
    $result=Join-Path $testRoot 'probe.json'
    $script=@'
Add-Type @"
using System;using System.Runtime.InteropServices;
public class ConsoleProbe {
 [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
 [DllImport("kernel32.dll")] public static extern uint GetConsoleProcessList(uint[] list,uint count);
}
"@
$processes=New-Object uint[] 8
# Exercise progress and native module loading, not just an empty PowerShell child.
$ProgressPreference='SilentlyContinue'
Write-Progress -Activity 'Pocket test' -Status 'loading'
Get-NetTCPConnection -State Listen -LocalPort 4317 -ErrorAction SilentlyContinue | Out-Null
Write-Output 'discarded diagnostic output'
Write-Error 'discarded diagnostic error' -ErrorAction Continue
@{consoleWindow=([ConsoleProbe]::GetConsoleWindow().ToInt64());consoleProcesses=[ConsoleProbe]::GetConsoleProcessList($processes,8)}|ConvertTo-Json|Set-Content (Join-Path $PSScriptRoot 'probe.json')
'@
    [IO.File]::WriteAllText($probe,$script)
    $p=Start-Process -FilePath $exe -ArgumentList ('"'+$probe+'"') -PassThru
    Assert-True ($p.WaitForExit(20000) -and $p.ExitCode -eq 0) 'GUI host exits after its script finishes'
    $deadline=[DateTime]::UtcNow.AddSeconds(20)
    while(-not(Test-Path -LiteralPath $result) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
    $state=Get-Content -LiteralPath $result -Raw|ConvertFrom-Json
    Assert-True ($state.consoleWindow -eq 0 -and $state.consoleProcesses -eq 0) 'hosted tray has no console attached'
    @{ok=$true;checks=$checks}|ConvertTo-Json -Compress
} finally {
    if(Test-Path -LiteralPath $testRoot){Remove-Item -LiteralPath $testRoot -Recurse -Force}
}
