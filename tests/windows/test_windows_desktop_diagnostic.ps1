param([Parameter(Mandatory=$true)][string]$ReportPath)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\platforms\windows\scripts\windows-desktop-diagnostic.ps1') -DefinitionsOnly

$script:checksPassed = 0
function Assert-Equal($Actual, $Expected, [string]$Reason) {
    if ($Actual -cne $Expected) { throw $Reason }
    $script:checksPassed++
}

$restore = [string][char]0x6062 + [char]0x590d
$send = [string][char]0x53d1 + [char]0x9001
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' $restore $false) $null 'Window Restore must be excluded.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' 'Send' $false) $null 'Non-document buttons must be excluded.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Text' 'Send' $true) $null 'Text must not be an action.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' 'private task title' $true) $null 'Private names must be omitted.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' "Send`n" $true) $null 'Only exact labels are allowed.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' $send $true) $send 'Chinese send must match.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' 'Stop' $true) 'Stop' 'English stop must match.'
Assert-Equal (Get-PocketScanStatus 0 0 0 $false) 'no_composer_controls' 'Window chrome is not a composer.'
Assert-Equal (Get-PocketScanStatus 0 0 1 $false) 'web_content_found_no_composer' 'A web document is not an input.'
Assert-Equal (Get-PocketScanStatus 1 1 1 $true) 'incomplete_scan' 'Truncation must remain explicit.'
Assert-Equal (Get-PocketScanStatus 1 1 1 $false) 'candidate_controls_found' 'Candidates are not verified actions.'
Assert-Equal (Get-PocketButtonHint 'ControlType.Button' 'Start new voice chat' $true) 'Start new voice chat' 'Voice hint must be separate.'
Assert-Equal (Get-PocketButtonHint 'ControlType.Button' 'Stop dictation' $true) 'Stop dictation' 'Dictation must remain identifiable.'
Assert-Equal (Get-PocketActionLabel 'ControlType.Button' 'Stop dictation' $true) $null 'Dictation stop is not task stop.'
Assert-Equal (Get-PocketButtonHint 'ControlType.Button' 'Start voice' $false) $null 'Only document buttons allowed.'
Assert-Equal (Get-PocketButtonHint 'ControlType.Button' 'private label' $true) $null 'Unknown hints must not leak.'
Assert-Equal (Get-PocketButtonHint 'ControlType.Button' "Send`n" $true) $null 'Hints must match exactly.'
. (Join-Path $PSScriptRoot '..\..\platforms\windows\scripts\windows-desktop-smoke-send.ps1') -DefinitionsOnly
$draft = 'Pocket Windows ' + [char]0x6d4b + [char]0x8bd5
Assert-Equal (Test-PocketSmokeDraft $draft) $true 'Only fixed draft allowed.'
Assert-Equal (Test-PocketSmokeDraft ($draft + ' ')) $false 'Do not trim or alter draft.'
Assert-Equal (Test-PocketSmokeDraft 'private draft') $false 'Do not overwrite or send unrelated draft.'
Assert-Equal (Test-PocketSendLabel $send) $true 'Chinese send accepted.'
Assert-Equal (Test-PocketSendLabel 'Stop') $false 'Stop is not send.'
Assert-Equal (Test-PocketSendLabel 'Start new voice chat') $false 'Voice is not send.'
Assert-Equal (Test-PocketSendLabel "Send`n") $false 'No partial send labels.'
. (Join-Path $PSScriptRoot '..\..\platforms\windows\scripts\windows-desktop-send.ps1') -DefinitionsOnly
$payload=[pscustomobject]@{threadId='11111111-1111-4111-8111-111111111111';expectedTitle='Current title';message='hello'}
Assert-Equal (Test-PocketDeliveryPayload $payload) $true 'Valid text delivery accepted.'
$payload.threadId='../unsafe'
Assert-Equal (Test-PocketDeliveryPayload $payload) $false 'Invalid target rejected.'
$payload.threadId='11111111-1111-4111-8111-111111111111'; $payload.message=' '
Assert-Equal (Test-PocketDeliveryPayload $payload) $false 'Empty message rejected.'
$payload.message='hello'; $payload | Add-Member -NotePropertyName command -NotePropertyValue 'unsafe'
Assert-Equal (Test-PocketDeliveryPayload $payload) $false 'Unknown payload field rejected.'
Assert-Equal (Test-PocketDocumentTitle 'Current title' 'Current title') $true 'Exact current title matches.'
Assert-Equal (Test-PocketDocumentTitle 'ChatGPT - Current title' 'Current title') $true 'Known product prefix accepted.'
Assert-Equal (Test-PocketDocumentTitle 'Current title extra' 'Current title') $false 'Substring title refused.'
$placeholder="`n" + [char]0x968f + [char]0x5fc3 + [char]0x8f93 + [char]0x5165
Assert-Equal (Test-PocketEmptyComposer '' @()) $true 'Truly empty editor accepted.'
Assert-Equal (Test-PocketEmptyComposer $placeholder @('Start voice chat')) $true 'Observed placeholder with idle voice action accepted.'
Assert-Equal (Test-PocketEmptyComposer $placeholder @('Start voice chat','Send')) $false 'Typed placeholder with Send remains a draft.'
Assert-Equal (Test-PocketEmptyComposer $placeholder @('Start voice chat','Stop')) $false 'Active task cannot use empty-placeholder exception.'
Assert-Equal (Test-PocketEmptyComposer $placeholder @()) $false 'Placeholder without idle evidence refused.'
Assert-Equal (Test-PocketEmptyComposer ($placeholder.Trim()) @('Start voice chat')) $false 'No arbitrary trimming.'
Assert-Equal (Test-PocketEmptyComposer 'private draft' @('Start voice chat')) $false 'Real draft preserved.'
$bridge=@{kind='ControlType.Pane';framework='Win32';enabled=$false;offscreen=$false;inheritedUsable=$true}
Assert-Equal (Resolve-PocketInheritedUsability $false 'ControlType.Document' 'Chrome' $bridge) $true 'Enabled Chrome document may cross one disabled Win32 bridge.'
Assert-Equal (Resolve-PocketInheritedUsability $false 'ControlType.Edit' 'Chrome' $bridge) $false 'Only document boundary can use bridge exception.'
Assert-Equal (Resolve-PocketInheritedUsability $false 'ControlType.Document' 'other' $bridge) $false 'Only Chrome provider allowed.'
$bridge.offscreen=$true
Assert-Equal (Resolve-PocketInheritedUsability $false 'ControlType.Document' 'Chrome' $bridge) $false 'Hidden provider stays unusable.'
$bridge.offscreen=$false;$bridge.inheritedUsable=$false
Assert-Equal (Resolve-PocketInheritedUsability $false 'ControlType.Document' 'Chrome' $bridge) $false 'Disabled outer ancestor stays unusable.'
$bridge.inheritedUsable=$true;$bridge.framework='Chrome'
Assert-Equal (Resolve-PocketInheritedUsability $false 'ControlType.Document' 'Chrome' $bridge) $false 'Disabled web ancestor stays unusable.'
Assert-Equal (Resolve-PocketInheritedUsability $true 'ControlType.Edit' 'Chrome' $null) $true 'Normal enabled ancestry unchanged.'
Assert-Equal (Test-PocketCanNavigateWithoutComposer 'ambiguous_editor' '') $true 'Text send may navigate away from an unready source composer.'
Assert-Equal (Test-PocketPreactivate '' $false) $true 'Normal text send activates before scanning.'
Assert-Equal (Test-PocketPreactivate 'prepare' $false) $false 'Stop prepare retains its existing flow.'
Assert-Equal (Test-PocketPreactivate 'commit' $false) $false 'Commit never reacquires foreground.'
Assert-Equal (Test-PocketPreactivate '' $true) $false 'Display-off experiment is unchanged.'
Assert-Equal (Test-PocketWindowVisibility $true $true $true) $true 'Minimized package window can be restored.'
Assert-Equal (Test-PocketWindowVisibility $true $false $true) $false 'Other hidden windows are not restored.'
Assert-Equal (Test-PocketWindowVisibility $true $true $false) $false 'Snapshot still requires a visible window.'
Assert-Equal (Test-PocketWindowVisibility $false $false $false) $true 'Visible window remains eligible.'
Assert-Equal (Test-PocketCanNavigateWithoutComposer 'ambiguous_editor' 'commit') $false 'Commit must not relax its source guard.'
Assert-Equal (Test-PocketCanNavigateWithoutComposer 'ambiguous_editor' 'prepare') $false 'Stop and Resume retain source guard.'
foreach($reason in @('ambiguous_window','desktop_locked_or_unavailable','incomplete_scan','unverified_composer')) {
    Assert-Equal (Test-PocketCanNavigateWithoutComposer $reason '') $false 'Only source editor ambiguity is recoverable by navigation.'
}
Assert-Equal (Test-PocketResumeComposer $placeholder @('Resume')) $true 'Paused placeholder can resume.'
Assert-Equal (Test-PocketResumeComposer '' @('Continue')) $true 'Empty composer can continue.'
Assert-Equal (Test-PocketResumeComposer 'private draft' @('Resume')) $false 'Resume never sends or removes draft.'
Assert-Equal (Test-PocketResumeComposer $placeholder @('Resume','Send')) $false 'Send conflicts with resume.'
Assert-Equal (Test-PocketResumeComposer '' @('Resume','Stop')) $false 'Stop conflicts with resume.'
Assert-Equal (Test-PocketResumeComposer '' @('Start voice')) $false 'Voice is never resume.'
Assert-Equal (Test-PocketResumeComposer '' @('Resume','Continue')) $false 'Ambiguous resume refused.'
Assert-Equal (Test-PocketResumeLabel "Resume`n") $false 'Only exact labels.'
Write-PocketDiagnosticReport @{label=$restore; testsPassed=$script:checksPassed} $ReportPath
