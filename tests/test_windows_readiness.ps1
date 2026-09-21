$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '..\scripts\windows-desktop-send.ps1') -DefinitionsOnly
function Assert-True($value,[string]$message){if(-not $value){throw $message}}
Assert-True ((Get-PocketScanBudget) -eq 10) 'Default post-write budget must not overflow'
Assert-True ((Get-PocketScanBudget ([DateTime]::UtcNow.AddSeconds(30))) -eq 10) 'Normal scan remains capped at ten seconds'
Assert-True ((Get-PocketScanBudget ([DateTime]::UtcNow.AddSeconds(-1))) -lt 0) 'Expired deadline must stay expired'
$state=@{reads=0}
$result=Invoke-PocketReadiness {
    $state.reads++
    if($state.reads -eq 1){throw 'scan_time_limit'}
    @{ready=$true}
} {param($s) $s.ready} ([DateTime]::UtcNow.AddSeconds(5))
Assert-True ($result.ready -and $state.reads -eq 2) 'Transient read must retry once and succeed'
foreach($reason in @('scan_node_limit','scan_depth_limit','desktop_locked_or_unavailable','ambiguous_window','foreground_task_changed','desktop_draft_present','unverified_composer','provider_error')){
    $state.reads=0;$caught=$null
    try{Invoke-PocketReadiness {$state.reads++;throw $reason} {$true} ([DateTime]::UtcNow.AddSeconds(5))|Out-Null}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -ceq $reason -and $state.reads -eq 1) ('Must not retry '+$reason)
}
foreach($reason in @('scan_provider_unavailable','ambiguous_editor','scan_time_limit')){
    $state.reads=0;$caught=$null
    try{Invoke-PocketReadiness {$state.reads++;throw $reason} {$true} ([DateTime]::UtcNow.AddSeconds(5))|Out-Null}catch{$caught=$_.Exception.Message}
    Assert-True ($caught -ceq $reason -and $state.reads -eq 3) 'Transient retries must be bounded'
}
$state.reads=0;$caught=$null
try{Invoke-PocketReadiness {$state.reads++;@{ready=$false}} {param($s) $s.ready} ([DateTime]::UtcNow.AddSeconds(5))|Out-Null}catch{$caught=$_.Exception.Message}
Assert-True ($caught -ceq 'task_identity_mismatch' -and $state.reads -eq 3) 'Wrong destination never becomes success'
$state.reads=0;$caught=$null
try{Invoke-PocketReadiness {$state.reads++} {$true} ([DateTime]::UtcNow.AddSeconds(-1))|Out-Null}catch{$caught=$_.Exception.Message}
Assert-True ($caught -ceq 'interface_not_ready' -and $state.reads -eq 0) 'Expired budget must not start another scan'
$state.reads=0;$caught=$null
$deadline=[DateTime]::UtcNow.AddMilliseconds(30)
try{Invoke-PocketReadiness {$state.reads++;Start-Sleep -Milliseconds 100;@{ready=$true}} {$true} $deadline|Out-Null}catch{$caught=$_.Exception.Message}
Assert-True ($caught -ceq 'interface_not_ready' -and $state.reads -eq 1) 'Late read is not readiness'
@{ok=$true;checks=18;writes=0}|ConvertTo-Json -Compress
