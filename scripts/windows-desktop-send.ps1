# Opt-in, text-only native delivery. JSON on stdin, no shell interpolation.
# Never clears/replaces drafts or presses Enter. Optional taskbar click only;
# composer submission always uses the verified UIA InvokePattern.
param([switch]$DefinitionsOnly, [ValidateSet('prepare','commit')][string]$StopPhase,
    [ValidateSet('prepare','commit')][string]$ResumePhase, [switch]$AllowDisplayOff,
    [switch]$AllowTaskbarActivation)
$ActionPhase=if($ResumePhase){$ResumePhase}else{$StopPhase}
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)

function Test-PocketDeliveryPayload($p) {
    if ($null -eq $p -or @($p.PSObject.Properties.Name).Count -ne 3) { return $false }
    if (@($p.PSObject.Properties.Name | Where-Object { $_ -notin @('threadId','expectedTitle','message') }).Count) { return $false }
    return $p.threadId -is [string] -and $p.threadId -cmatch '\A[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\z' -and
        $p.expectedTitle -is [string] -and $p.expectedTitle.Length -gt 0 -and $p.expectedTitle.Length -le 1000 -and
        $p.message -is [string] -and -not [string]::IsNullOrWhiteSpace($p.message) -and $p.message.Length -le 20000 -and
        -not $p.message.Contains([char]0)
}
function Test-PocketDocumentTitle([string]$Actual, [string]$Expected) {
    return $Actual -ceq $Expected -or $Actual -ceq ('ChatGPT - ' + $Expected)
}
function Test-PocketCanNavigateWithoutComposer([string]$Reason, [string]$Phase) {
    # A cold/home page may have no unique editor. Navigation never writes to
    # that source composer; the destination still needs every normal guard.
    return [string]::IsNullOrEmpty($Phase) -and $Reason -ceq 'ambiguous_editor'
}
function Test-PocketPreactivate([string]$Phase, [bool]$DisplayOffExperiment) {
    # Do not change the two-phase Stop/Resume or display-off protocols.
    return [string]::IsNullOrEmpty($Phase) -and -not $DisplayOffExperiment
}
function Test-PocketWindowVisibility([bool]$Offscreen, [bool]$Iconic, [bool]$IncludeMinimized) {
    return -not $Offscreen -or ($IncludeMinimized -and $Iconic)
}
function Invoke-PocketReadiness([scriptblock]$Read, [scriptblock]$Matches, [datetime]$Deadline) {
    # Read-only callbacks only. Never wrap SetValue or Invoke in this loop.
    for($attempt=1;$attempt -le 3;$attempt++) {
        if([DateTime]::UtcNow -ge $Deadline){throw 'interface_not_ready'}
        try {
            $snapshot=& $Read
            if([DateTime]::UtcNow -ge $Deadline){throw 'interface_not_ready'}
            if(& $Matches $snapshot){return $snapshot}
            throw 'task_identity_mismatch'
        } catch {
            $reason=$_.Exception.Message
            if($reason -notin @('scan_time_limit','scan_provider_unavailable','ambiguous_editor','task_identity_mismatch')){throw}
            if($attempt -eq 3 -or [DateTime]::UtcNow -ge $Deadline){throw}
        }
        Start-Sleep -Milliseconds 250
    }
}
function Get-PocketScanBudget([datetime]$Deadline=[datetime]::MaxValue) {
    # Explicit double prevents PowerShell 5.1 selecting Int32 Min and
    # overflowing on the default (unbounded overall) deadline after writing.
    return [Math]::Min([double]10,($Deadline-[DateTime]::UtcNow).TotalSeconds)
}
function Test-PocketDisplayOffRoute([bool]$OptIn, [bool]$Unlocked, [int]$DisplayState,
    [long]$Foreground, [bool]$LogonUiPresent, [bool]$IdentityStable) {
    return $OptIn -and $Unlocked -and $DisplayState -eq 0 -and $Foreground -eq 0 -and
        -not $LogonUiPresent -and $IdentityStable
}
function Test-PocketStopPayload($p) {
    if ($null -eq $p -or @($p.PSObject.Properties.Name).Count -ne 3) { return $false }
    if (@($p.PSObject.Properties.Name | Where-Object { $_ -notin @('threadId','expectedTitle','guard') }).Count) { return $false }
    if (-not ($p.threadId -is [string] -and $p.threadId -cmatch '\A[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\z' -and
        $p.expectedTitle -is [string] -and $p.expectedTitle.Length -gt 0 -and $p.expectedTitle.Length -le 1000)) { return $false }
    if ($ActionPhase -eq 'prepare') { return $null -eq $p.guard }
    $g=$p.guard
    if ($null -eq $g -or @($g.PSObject.Properties.Name).Count -ne 3 -or
        @($g.PSObject.Properties.Name | Where-Object { $_ -notin @('buttonId','documentId','issuedAt') }).Count) { return $false }
    foreach($id in @('buttonId','documentId')) {
        if ($g.$id -isnot [array] -or $g.$id.Count -lt 1 -or $g.$id.Count -gt 32) { return $false }
        foreach($n in $g.$id) { if ($n -isnot [int] -and $n -isnot [long]) { return $false } }
    }
    return $g.issuedAt -is [long] -or $g.issuedAt -is [int]
}
function Test-PocketStopGuardFresh($guard) {
    $age=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-$guard.issuedAt
    return $age -ge 0 -and $age -le 8000
}
function Test-PocketEmptyComposer([string]$Value, [string[]]$ButtonNames) {
    if ([string]::IsNullOrEmpty($Value)) { return $true }
    # Chromium UIA includes the empty editor's CSS placeholder in Value/Text.
    # Only the observed exact form is supported; never trim arbitrary drafts.
    if ($Value -cnotmatch '\A\n\u968f\u5fc3\u8f93\u5165\z') { return $false }
    $voice=$false
    foreach($name in $ButtonNames) {
        if ($name -cmatch '\A(Send|Send message|Submit|Stop|Stop generating|Stop response|Queue|Steer|Resume|Continue|\u53d1\u9001|\u53d1\u9001\u6d88\u606f|\u63d0\u4ea4|\u505c\u6b62|\u505c\u6b62\u751f\u6210|\u52a0\u5165\u961f\u5217|\u8c03\u6574\u65b9\u5411|\u7ee7\u7eed|\u6062\u590d|\u50b3\u9001|\u50b3\u9001\u8a0a\u606f|\u767c\u9001)\z') { return $false }
        if ($name -cmatch '\A(Start voice|Start voice chat|Start new voice chat|\u5f00\u59cb\u8bed\u97f3|\u5f00\u542f\u8bed\u97f3\u804a\u5929|\u5f00\u59cb\u8bed\u97f3\u804a\u5929|\u5f00\u59cb\u65b0\u7684\u8bed\u97f3\u804a\u5929)\z') { $voice=$true }
    }
    return $voice
}
function Resolve-PocketInheritedUsability([bool]$Inherited, [string]$Kind, [string]$Framework, $Parent) {
    if ($Inherited) { return $true }
    # Chromium's enabled web document can sit directly under a disabled Win32
    # provider bridge. Exempt only that one pane, not disabled web ancestors,
    # hidden panes, disabled windows, or arbitrary nested provider structures.
    return $Kind -ceq 'ControlType.Document' -and $Framework -ceq 'Chrome' -and
        $null -ne $Parent -and $Parent.kind -ceq 'ControlType.Pane' -and
        $Parent.framework -ceq 'Win32' -and -not $Parent.enabled -and
        -not $Parent.offscreen -and $Parent.inheritedUsable
}
function Test-PocketResumeLabel([string]$Name) {
    return $Name -cmatch '\A(Resume|Continue|Resume task|Continue task|\u7ee7\u7eed|\u6062\u590d|\u7ee7\u7eed\u4efb\u52a1|\u7e7c\u7e8c)\z'
}
function Test-PocketResumeComposer([string]$Value, [string[]]$Names) {
    if (-not ([string]::IsNullOrEmpty($Value) -or $Value -cmatch '\A\n\u968f\u5fc3\u8f93\u5165\z')) { return $false }
    $count=0
    foreach($name in $Names) {
        if(Test-PocketResumeLabel $name){$count++}
        if($name -cmatch '\A(Send|Send message|Submit|Stop|Stop generating|Stop response|Queue|Steer|\u53d1\u9001|\u53d1\u9001\u6d88\u606f|\u63d0\u4ea4|\u505c\u6b62|\u505c\u6b62\u751f\u6210|\u52a0\u5165\u961f\u5217|\u8c03\u6574\u65b9\u5411|\u50b3\u9001|\u50b3\u9001\u8a0a\u606f|\u767c\u9001)\z'){return $false}
    }
    return $count -eq 1
}
if ($DefinitionsOnly) { return }
$result=@{status='refused'; reason='preflight_failed'; targetThreadId=$null}
$displayObserver=$null
$targetIdentity=$null
$scanLogs=[Collections.Generic.List[object]]::new()
$activationLogs=[Collections.Generic.List[object]]::new()
try {
    if($StopPhase -and $ResumePhase){throw 'invalid_payload'}
    if($AllowDisplayOff -and $ActionPhase){throw 'invalid_payload'} # Text send only in this experiment.
    if($AllowTaskbarActivation -and ($ActionPhase -or $AllowDisplayOff)){throw 'invalid_payload'}
    $raw=[Console]::In.ReadToEnd()
    if ($raw.Length -gt 180000) { throw 'invalid_payload' }
    $payload=$raw | ConvertFrom-Json
    if ($ActionPhase) {
        if (-not (Test-PocketStopPayload $payload)) { throw 'invalid_payload' }
    } elseif (-not (Test-PocketDeliveryPayload $payload)) { throw 'invalid_payload' }
    $result.targetThreadId=$payload.threadId
    $session=(Get-Process -Id $PID).SessionId
    if ($session -eq 0) { throw 'interactive_session_required' }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -Path (Join-Path $PSScriptRoot 'windows-session-state.cs')
    Add-Type @'
using System;
using System.Text;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class PocketDeliveryDesktop {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
}
'@
    if (-not [PocketWindowsSession]::IsUnlocked()) { throw 'desktop_locked_or_unavailable' }
    if($AllowDisplayOff) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -Path (Join-Path $PSScriptRoot 'windows-display-state.cs') -ReferencedAssemblies System.Windows.Forms
        $displayObserver=[PocketDisplayState]::new()
        $until=[DateTime]::UtcNow.AddSeconds(1)
        while($displayObserver.Read() -eq -1 -and [DateTime]::UtcNow -lt $until){Start-Sleep -Milliseconds 50}
    }
    $packages=@(Get-AppxPackage -Name OpenAI.Codex)
    $ids=@(Get-Process ChatGPT,Codex -ErrorAction SilentlyContinue | Where-Object {
        $p=$_; $ok=$false
        foreach($package in $packages) {
            if($p.Path -and $p.Path.StartsWith($package.InstallLocation+'\',[StringComparison]::OrdinalIgnoreCase)) {$ok=$true}
        }
        $ok -and $p.SessionId -eq $session
    } | Select-Object -ExpandProperty Id)
    $walker=[Windows.Automation.TreeWalker]::RawViewWalker
    function Get-PocketWindow([bool]$IncludeMinimized=$false) {
        if (-not [PocketWindowsSession]::IsUnlocked()) { throw 'desktop_locked_or_unavailable' }
        $windows=@([Windows.Automation.AutomationElement]::RootElement.FindAll(
            [Windows.Automation.TreeScope]::Children,[Windows.Automation.Condition]::TrueCondition) |
            Where-Object { $_.Current.ProcessId -in $ids -and
                (Test-PocketWindowVisibility $_.Current.IsOffscreen ([PocketDeliveryDesktop]::IsIconic([IntPtr]$_.Current.NativeWindowHandle)) $IncludeMinimized) })
        if ($windows.Count -ne 1) { throw 'ambiguous_window' }
        return $windows[0]
    }
    function Assert-PocketWindowHandle($window) {
        if (-not [PocketWindowsSession]::IsUnlocked()) { throw 'desktop_locked_or_unavailable' }
        $handle=[IntPtr]$window.Current.NativeWindowHandle
        $owner=[uint32]0
        [void][PocketDeliveryDesktop]::GetWindowThreadProcessId($handle,[ref]$owner)
        if ($handle -eq [IntPtr]::Zero -or -not [PocketDeliveryDesktop]::IsWindow($handle) -or
            $owner -notin $ids -or $owner -ne $window.Current.ProcessId) { throw 'ambiguous_window' }
        return $handle
    }
    function Request-PocketForeground($window, [bool]$RestoreMinimized=$false) {
        $handle=Assert-PocketWindowHandle $window
        if ($RestoreMinimized -and [PocketDeliveryDesktop]::IsIconic($handle)) {
            [void][PocketDeliveryDesktop]::ShowWindowAsync($handle,9) # SW_RESTORE, not maximize/topmost.
            $result.restoredWindow=$true
            $until=[DateTime]::UtcNow.AddSeconds(2)
            while ([PocketDeliveryDesktop]::IsIconic($handle)) {
                [void](Assert-PocketWindowHandle $window)
                if ([DateTime]::UtcNow -ge $until) { throw 'window_restore_unconfirmed' }
                Start-Sleep -Milliseconds 100
            }
        }
        [void](Assert-PocketWindowHandle $window)
        if ([PocketDeliveryDesktop]::GetForegroundWindow() -ne $handle) {
            $result.activationRequested=$true
            # Record the API return separately from actual HWND ownership.
            # UIA SetFocus and AppActivate were not reliable under real manual
            # foreground input; do not stack ineffective activation fallbacks.
            $activation=@{win32Accepted=[PocketDeliveryDesktop]::SetForegroundWindow($handle);confirmed=$false}
            $until=[DateTime]::UtcNow.AddSeconds(2)
            while ([PocketDeliveryDesktop]::GetForegroundWindow() -ne $handle -and [DateTime]::UtcNow -lt $until) {
                [void](Assert-PocketWindowHandle $window)
                Start-Sleep -Milliseconds 100
            }
            $activation.confirmed=[PocketDeliveryDesktop]::GetForegroundWindow() -eq $handle
            if($activationLogs.Count -lt 4){$activationLogs.Add($activation)}
        }
        # A request is not proof of activation. Shell navigation can still bring
        # the app forward; the final target guard must succeed before any write.
        return [PocketDeliveryDesktop]::GetForegroundWindow() -eq $handle
    }
    function Get-PocketSnapshot([string]$Stage='single', [datetime]$Deadline=[datetime]::MaxValue) {
        $clock=[Diagnostics.Stopwatch]::StartNew()
        $scan=@{stage=$Stage;nodes=0;queued=0;reason='ok';elapsedMs=0}
        $budget=Get-PocketScanBudget $Deadline
        try {
        if($budget -le 0){throw 'interface_not_ready'}
        $window=Get-PocketWindow
        $queue=[Collections.Queue]::new()
        $queue.Enqueue(@{element=$window; parent=-1; depth=0; document=$false; usable=$true})
        $nodes=[Collections.Generic.List[object]]::new()
        while($queue.Count) {
            $scan.nodes=$nodes.Count;$scan.queued=$queue.Count
            if ($clock.Elapsed.TotalSeconds -ge $budget) { throw 'scan_time_limit' }
            if ($nodes.Count -ge 1200) { throw 'scan_node_limit' }
            $e=$queue.Dequeue(); $c=$e.element.Current; $index=$nodes.Count
            $doc=$e.document -or $c.ControlType -eq [Windows.Automation.ControlType]::Document
            $parentNode=if($e.parent -ge 0){$nodes[$e.parent]}else{$null}
            $inherited=Resolve-PocketInheritedUsability $e.usable $c.ControlType.ProgrammaticName $c.FrameworkId $parentNode
            $usable=$inherited -and $c.IsEnabled -and -not $c.IsOffscreen
            $nodes.Add(@{element=$e.element; parent=$e.parent; document=$doc; usable=$usable;
                inheritedUsable=$inherited;kind=$c.ControlType.ProgrammaticName;framework=$c.FrameworkId;
                enabled=$c.IsEnabled;offscreen=$c.IsOffscreen})
            $child=$walker.GetFirstChild($e.element)
            if ($child -and $e.depth -ge 64) { throw 'scan_depth_limit' }
            while($child) {
                $scan.nodes=$nodes.Count;$scan.queued=$queue.Count
                if ($clock.Elapsed.TotalSeconds -ge $budget) { throw 'scan_time_limit' }
                if ($nodes.Count+$queue.Count -ge 1200) { throw 'scan_node_limit' }
                $queue.Enqueue(@{element=$child;parent=$index;depth=$e.depth+1;document=$doc;usable=$usable})
                $child=$walker.GetNextSibling($child)
            }
        }
        $scan.nodes=$nodes.Count;$scan.queued=$queue.Count
        if($clock.Elapsed.TotalSeconds -ge $budget){throw 'scan_time_limit'}
        $documents=@($nodes | Where-Object { $_.usable -and $_.element.Current.ControlType -eq [Windows.Automation.ControlType]::Document -and $_.element.Current.FrameworkId -eq 'Chrome' })
        $edits=@($nodes | Where-Object { $_.usable -and $_.document -and $_.element.Current.ControlType -eq [Windows.Automation.ControlType]::Edit -and $_.element.Current.FrameworkId -eq 'Chrome' })
        if ($documents.Count -ne 1 -or $edits.Count -ne 1) { throw 'ambiguous_editor' }
        $edit=$edits[0]; $value=$null
        if (-not $edit.element.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or $value.Current.IsReadOnly) { throw 'editor_not_writable' }
        if ($edit.parent -lt 0 -or $nodes[$edit.parent].element.Current.ControlType -ne [Windows.Automation.ControlType]::Group) { throw 'unverified_composer' }
        if($clock.Elapsed.TotalSeconds -ge $budget){throw 'scan_time_limit'}
        return @{window=$window; nodes=$nodes; document=$documents[0].element; edit=$edit; value=$value}
        } catch {
            if($_.Exception.GetBaseException() -is [Windows.Automation.ElementNotAvailableException]) {
                $scan.reason='scan_provider_unavailable'
                throw 'scan_provider_unavailable'
            }
            $reason=$_.Exception.Message
            $scan.reason=if($reason -in @('interface_not_ready','scan_time_limit','scan_node_limit','scan_depth_limit','ambiguous_window','ambiguous_editor','desktop_locked_or_unavailable','editor_not_writable','unverified_composer')){$reason}else{'provider_error'}
            throw
        } finally {
            $scan.elapsedMs=[int]$clock.ElapsedMilliseconds
            if($scanLogs.Count -lt 8){$scanLogs.Add($scan)}
        }
    }
    function Get-PocketTargetIdentity($snapshot) {
        return (@($snapshot.window.GetRuntimeId()) -join ',')+'|'+
            (@($snapshot.document.GetRuntimeId()) -join ',')+'|'+
            (@($snapshot.edit.element.GetRuntimeId()) -join ',')
    }
    function Test-PocketObservedDisplayOff {
        if(-not $displayObserver){return $false}
        $logon=@(Get-Process LogonUI -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -eq $session}).Count -gt 0
        if($displayObserver.Read() -eq 0 -and $logon){throw 'display_off_security_state_unconfirmed'}
        return Test-PocketDisplayOffRoute ([bool]$AllowDisplayOff) ([PocketWindowsSession]::IsUnlocked()) `
            ($displayObserver.Read()) ([PocketDeliveryDesktop]::GetForegroundWindow().ToInt64()) $logon $true
    }
    function Assert-PocketTarget($snapshot) {
        if (-not [PocketWindowsSession]::IsUnlocked()) { throw 'desktop_locked_or_unavailable' }
        if (-not (Test-PocketDocumentTitle $snapshot.document.Current.Name $payload.expectedTitle)) { throw 'task_identity_mismatch' }
        if (-not $snapshot.edit.element.Current.IsEnabled -or $snapshot.edit.element.Current.IsOffscreen) { throw 'editor_changed' }
        if($targetIdentity -and (Get-PocketTargetIdentity $snapshot) -cne $targetIdentity){throw 'editor_changed'}
        if ([PocketDeliveryDesktop]::GetForegroundWindow().ToInt64() -ne $snapshot.window.Current.NativeWindowHandle) {
            if(-not $targetIdentity -or -not (Test-PocketObservedDisplayOff)){throw 'foreground_task_changed'}
            $result.displayOffRoute=$true
        }
    }
    function Get-PocketComposerButtons($snapshot) {
        return @($snapshot.nodes | Where-Object {
            $n=$_; $c=$n.element.Current; $near=$false
            for($i=0;$i -lt 3 -and $n.parent -ge 0;$i++) {
                if ($n.parent -eq $snapshot.edit.parent) {$near=$true;break}
                $n=$snapshot.nodes[$n.parent]
            }
            $near -and $_.usable -and $_.document -and $c.ControlType -eq [Windows.Automation.ControlType]::Button
        })
    }
    if (Test-PocketPreactivate $ActionPhase ([bool]$AllowDisplayOff)) {
        $sourceWindow=Get-PocketWindow $true
        $result.activatedBeforeScan=Request-PocketForeground $sourceWindow $true
    }
    $readyText=Test-PocketPreactivate $ActionPhase ([bool]$AllowDisplayOff)
    $readyDeadline=[DateTime]::UtcNow.AddSeconds(24)
    $snapshot=$null
    try {
        if($readyText){$snapshot=Invoke-PocketReadiness {Get-PocketSnapshot 'source' $readyDeadline} {$true} $readyDeadline}
        else{$snapshot=Get-PocketSnapshot 'source'}
        $result.switchedTask=-not (Test-PocketDocumentTitle $snapshot.document.Current.Name $payload.expectedTitle)
    } catch {
        if(-not (Test-PocketCanNavigateWithoutComposer $_.Exception.Message $ActionPhase)){throw}
        $result.switchedTask=$null
    }
    # Navigation does not write to the source composer. Its draft or paused
    # placeholder must not block delivery to a different, idle task. Check the
    # destination only after navigation and live identity verification below.
    # Only a validated UUID enters this fixed URI. Opening a task is not evidence
    # of successful navigation; the live document title is checked afterward.
    if ($ActionPhase -ne 'commit') {
        Start-Process -FilePath ('codex://threads/'+$payload.threadId) | Out-Null
        Start-Sleep -Milliseconds 700
        if($readyText) {
            $snapshot=Invoke-PocketReadiness {Get-PocketSnapshot 'destination' $readyDeadline} {
                param($candidate)
                Test-PocketDocumentTitle $candidate.document.Current.Name $payload.expectedTitle
            } $readyDeadline
        } else {
        $deadline=[DateTime]::UtcNow.AddSeconds(3)
        do {
            try {
                $snapshot=Get-PocketSnapshot
                if(Test-PocketDocumentTitle $snapshot.document.Current.Name $payload.expectedTitle){break}
            } catch {
                if($_.Exception.Message -cne 'ambiguous_editor'){throw}
                $snapshot=$null
            }
            Start-Sleep -Milliseconds 200
        } while([DateTime]::UtcNow -lt $deadline)
        if($null -eq $snapshot){throw 'ambiguous_editor'}
        }
    }
    # Windows may route the deep link without foregrounding the existing app.
    # Request activation only for the uniquely verified Codex package window;
    # if the OS refuses, Assert-PocketTarget still prevents writing.
    if ($ActionPhase -ne 'commit' -and [PocketDeliveryDesktop]::GetForegroundWindow().ToInt64() -ne $snapshot.window.Current.NativeWindowHandle -and
        -not (Test-PocketObservedDisplayOff)) {
        [void](Request-PocketForeground $snapshot.window)
        if($readyText){$snapshot=Invoke-PocketReadiness {Get-PocketSnapshot 'foreground' $readyDeadline} {
            param($candidate)
            Test-PocketDocumentTitle $candidate.document.Current.Name $payload.expectedTitle
        } $readyDeadline}else{$snapshot=Get-PocketSnapshot 'foreground'}
    }
    if($readyText -and $AllowTaskbarActivation -and
        [PocketDeliveryDesktop]::GetForegroundWindow().ToInt64() -ne $snapshot.window.Current.NativeWindowHandle) {
        . (Join-Path $PSScriptRoot 'windows-taskbar-activate.ps1')
        $result.taskbarActivation=Invoke-PocketTaskbarActivation $snapshot.window $packages {
            [void](Assert-PocketWindowHandle $snapshot.window)
            if(-not (Test-PocketDocumentTitle $snapshot.document.Current.Name $payload.expectedTitle)){throw 'task_identity_mismatch'}
        }
        # Fresh controls after activation; never reuse a pre-click Send button.
        $snapshot=Get-PocketSnapshot 'taskbar'
    }
    if($AllowDisplayOff){$targetIdentity=Get-PocketTargetIdentity $snapshot}
    Assert-PocketTarget $snapshot
    $buttons=Get-PocketComposerButtons $snapshot
    if ($ActionPhase) {
        if($ResumePhase -and -not (Test-PocketResumeComposer $snapshot.value.Current.Value @($buttons | ForEach-Object {$_.element.Current.Name}))) {
            throw 'resume_composer_not_empty_or_unavailable'
        }
        $stops=@($buttons | Where-Object {
            $c=$_.element.Current
            $(if($ResumePhase){Test-PocketResumeLabel $c.Name}else{$c.Name -cmatch '\A(Stop|Stop generating|Stop response|\u505c\u6b62|\u505c\u6b62\u751f\u6210|\u505c\u6b62\u54cd\u5e94|\u505c\u6b62\u7522\u751f)\z'}) -and
            ([string]::IsNullOrEmpty($c.HelpText) -or $c.HelpText -ceq $c.Name)
        })
        if ($stops.Count -ne 1) { throw 'stop_button_unavailable' }
        $button=$stops[0].element; $invoke=$null
        if (-not $button.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) { throw 'invoke_unavailable' }
        $guard=@{buttonId=@($button.GetRuntimeId());documentId=@($snapshot.document.GetRuntimeId());issuedAt=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()}
        if ($ActionPhase -eq 'prepare') {
            $result.status='prepared';$result.reason='recheck_turn_before_commit';$result.guard=$guard
        } else {
            if (-not (Test-PocketStopGuardFresh $payload.guard)) { throw 'stop_guard_expired' }
            if($ResumePhase -and -not (Test-PocketResumeComposer $snapshot.value.Current.Value @($buttons | ForEach-Object {$_.element.Current.Name}))) {
                throw 'resume_composer_not_empty_or_unavailable'
            }
            if (($guard.buttonId -join ',') -cne ($payload.guard.buttonId -join ',') -or
                ($guard.documentId -join ',') -cne ($payload.guard.documentId -join ',')) { throw 'stop_button_changed' }
            Assert-PocketTarget $snapshot
            if (-not $button.Current.IsEnabled -or $button.Current.IsOffscreen) { throw 'button_changed' }
            if (-not (Test-PocketStopGuardFresh $payload.guard)) { throw 'stop_guard_expired' }
            $result.status='uncertain';$result.reason='stop_invoke_attempted'
            $invoke.Invoke()
            $result.status='invoke_requested';$result.reason='verify_turn_finished'
        }
        $result | ConvertTo-Json -Depth 5 -Compress
        return
    }
    if (-not (Test-PocketEmptyComposer $snapshot.value.Current.Value @($buttons | ForEach-Object { $_.element.Current.Name }))) {
        $result.draftScope='verified_target'
        throw 'desktop_draft_present'
    }
    foreach($b in $buttons) {
        if ($b.element.Current.Name -match '\A(Stop|Stop generating|Stop response|Queue|Steer|Resume|Continue|\u505c\u6b62|\u505c\u6b62\u751f\u6210|\u52a0\u5165\u961f\u5217|\u8c03\u6574\u65b9\u5411|\u7ee7\u7eed|\u6062\u590d)\z') { throw 'desktop_turn_active_or_paused' }
    }
    if($AllowDisplayOff -and @($buttons|Where-Object {$_.element.Current.Name -cmatch '\A(Send|Send message|Submit|\u53d1\u9001|\u53d1\u9001\u6d88\u606f|\u63d0\u4ea4|\u50b3\u9001|\u50b3\u9001\u8a0a\u606f|\u767c\u9001)\z'}).Count){throw 'desktop_draft_present'}
    # Recheck the live element and display/session evidence immediately before
    # a UIA write. This never uses keyboard input, clipboard, or coordinates.
    Assert-PocketTarget $snapshot
    if(-not (Test-PocketEmptyComposer $snapshot.value.Current.Value @($buttons|ForEach-Object {$_.element.Current.Name}))){throw 'desktop_draft_present'}
    # From this point any failure leaves the draft intact and is uncertain.
    $result.status='uncertain'; $result.reason='draft_write_attempted'
    $snapshot.value.SetValue($payload.message)
    Start-Sleep -Milliseconds 200
    $snapshot=Get-PocketSnapshot 'after_write'
    Assert-PocketTarget $snapshot
    if ($snapshot.value.Current.Value -cne $payload.message) { throw 'draft_write_unconfirmed' }
    $buttons=@(Get-PocketComposerButtons $snapshot | Where-Object {
        $_.element.Current.Name -cmatch '\A(Send|Send message|Submit|\u53d1\u9001|\u53d1\u9001\u6d88\u606f|\u63d0\u4ea4|\u50b3\u9001|\u50b3\u9001\u8a0a\u606f|\u767c\u9001)\z'
    })
    if ($buttons.Count -ne 1) { throw 'send_button_unavailable' }
    $invoke=$null
    if (-not $buttons[0].element.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) { throw 'invoke_unavailable' }
    Assert-PocketTarget $snapshot
    if ($snapshot.value.Current.Value -cne $payload.message) { throw 'draft_changed' }
    if (-not $buttons[0].element.Current.IsEnabled -or $buttons[0].element.Current.IsOffscreen) { throw 'button_changed' }
    $result.reason='invoke_attempted'
    $invoke.Invoke()
    $result.status='invoke_requested'; $result.reason='verify_in_thread_history'
} catch {
    # Type/code are diagnostic metadata, never provider messages or UI text.
    $result.failureType=$_.Exception.GetBaseException().GetType().FullName
    $result.failureCode=$_.Exception.GetBaseException().HResult
    $allowed=@('invalid_payload','interactive_session_required','desktop_locked_or_unavailable','ambiguous_window','incomplete_scan',
        'ambiguous_editor','editor_not_writable','unverified_composer','foreground_task_changed','task_identity_mismatch','editor_changed',
        'desktop_draft_present','desktop_turn_active_or_paused','draft_write_unconfirmed','send_button_unavailable','invoke_unavailable','draft_changed','button_changed',
        'stop_button_unavailable','stop_guard_expired','stop_button_changed','resume_composer_not_empty_or_unavailable',
        'display_off_security_state_unconfirmed','window_restore_unconfirmed','interface_not_ready',
        'scan_time_limit','scan_node_limit','scan_depth_limit','scan_provider_unavailable',
        'taskbar_input_unavailable','taskbar_input_changed','taskbar_identity_unavailable','taskbar_scan_incomplete',
        'taskbar_button_unavailable','taskbar_button_changed','taskbar_button_obscured',
        'taskbar_click_unconfirmed','taskbar_activation_unconfirmed')
    $safeReason=$_.Exception.GetBaseException().Message
    if ($safeReason -in $allowed) {$result.reason=$safeReason}
} finally {
    $result.scans=@($scanLogs.ToArray())
    $result.activations=@($activationLogs.ToArray())
    if($displayObserver){$displayObserver.Dispose()}
}
$result | ConvertTo-Json -Depth 5 -Compress
