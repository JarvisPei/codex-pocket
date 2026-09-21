$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '..\..\platforms\windows\scripts\windows-taskbar-activate.ps1')
Add-Type -Path (Join-Path $PSScriptRoot '..\..\platforms\windows\scripts\windows-taskbar-input.cs')
function Assert-True($v,[string]$m){if(-not $v){throw $m}}
$id='Appid: OpenAI.Codex_test!App'
$c=@{id=$id;kind='ControlType.Button';class='Taskbar.TaskListButtonAutomationPeer';enabled=$true;offscreen=$false}
Assert-True (Test-PocketTaskbarCandidate $c $id) 'Exact visible app button'
foreach($bad in @('Codex','Appid: Other.Codex_test!App',($id+' extra'),$id.ToLowerInvariant())){
 $copy=$c.Clone();$copy.id=$bad
 Assert-True (-not (Test-PocketTaskbarCandidate $copy $id)) 'No fuzzy IDs or localized names'
}
foreach($pair in @(@('kind','ControlType.Pane'),@('class','OtherPeer'),@('enabled',$false),@('offscreen',$true))){
 $copy=$c.Clone();$copy[$pair[0]]=$pair[1]
 Assert-True (-not (Test-PocketTaskbarCandidate $copy $id)) 'Hidden/disabled/wrong-provider control refused'
}
Assert-True ([PocketTaskbarInput]::InputSize() -eq $(if([IntPtr]::Size -eq 8){40}else{28})) 'Native INPUT ABI must match Windows'
$apps=@(@{Id='App';Executable='app/ChatGPT.exe'},@{Id='Runner';Executable='app/runner.exe'})
Assert-True ((Get-PocketTaskbarAppId $apps 'C:\Package' 'C:\Package\app\ChatGPT.exe') -ceq 'App') 'Match real window process, not other package entrypoints'
foreach($set in @(@(@{Id='Wrong';Executable='runner.exe'}),@($apps[0],$apps[0]))){
 $caught=$null
 try{Get-PocketTaskbarAppId $set 'C:\Package' 'C:\Package\app\ChatGPT.exe'|Out-Null}catch{$caught=$_.Exception.Message}
 Assert-True ($caught -ceq 'taskbar_identity_unavailable') 'Missing/ambiguous executable match must refuse'
}
@{ok=$true;checks=13;clicks=0}|ConvertTo-Json -Compress
