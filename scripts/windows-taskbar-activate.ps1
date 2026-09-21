# Explicit opt-in: one physical click on an exact package-AUMID taskbar button.
# UIA gives physical geometry; hit testing is repeated after moving the pointer.
function Test-PocketTaskbarCandidate($c,[string]$Expected) {
    return $c.id -ceq $Expected -and $c.kind -ceq 'ControlType.Button' -and
        $c.class -ceq 'Taskbar.TaskListButtonAutomationPeer' -and $c.enabled -and -not $c.offscreen
}
function Get-PocketTaskbarAppId($Applications,[string]$InstallLocation,[string]$ProcessPath) {
    $matched=@($Applications|Where-Object {
        $_.Id -and $_.Executable -and
        [IO.Path]::GetFullPath((Join-Path $InstallLocation ([string]$_.Executable))) -ieq $ProcessPath
    })
    if($matched.Count -ne 1){throw 'taskbar_identity_unavailable'}
    return [string]$matched[0].Id
}
function Invoke-PocketTaskbarActivation($Window,$Packages,[scriptblock]$Validate) {
    Add-Type -Path (Join-Path $PSScriptRoot 'windows-taskbar-input.cs')
    $dpi=[PocketTaskbarInput]::SetThreadDpiAwarenessContext([IntPtr](-4))
    if($dpi -eq [IntPtr]::Zero){throw 'taskbar_input_unavailable'}
    $moved=$false;$point=$null;$original=[PocketTaskbarInput+Point]::new()
    try {
        & $Validate | Out-Null
        $codex=[IntPtr]$Window.Current.NativeWindowHandle
        if([PocketTaskbarInput]::GetForegroundWindow() -eq $codex){return @{status='already_foreground';clicks=0}}
        if(@($Packages).Count -ne 1){throw 'taskbar_identity_unavailable'}
        $apps=@(($Packages[0]|Get-AppxPackageManifest).Package.Applications.Application)
        $appId=Get-PocketTaskbarAppId $apps $Packages[0].InstallLocation (Get-Process -Id $Window.Current.ProcessId).Path
        $expected='Appid: '+$Packages[0].PackageFamilyName+'!'+$appId
        $bar=[PocketTaskbarInput]::FindWindow('Shell_TrayWnd',$null)
        $owner=[uint32]0
        [void][PocketTaskbarInput]::GetWindowThreadProcessId($bar,[ref]$owner)
        $shell=Get-Process -Id $owner -ErrorAction Stop
        if($bar -eq [IntPtr]::Zero -or $shell.SessionId -ne (Get-Process -Id $PID).SessionId -or
            $shell.Path -ine (Join-Path $env:SystemRoot 'explorer.exe')){throw 'taskbar_identity_unavailable'}
        $root=[Windows.Automation.AutomationElement]::FromHandle($bar)
        $walk=[Windows.Automation.TreeWalker]::RawViewWalker
        $queue=[Collections.Queue]::new();$queue.Enqueue(@{el=$root;depth=0})
        $found=[Collections.Generic.List[object]]::new();$taskButtons=[Collections.Generic.List[object]]::new()
        $nodes=0;$clock=[Diagnostics.Stopwatch]::StartNew()
        while($queue.Count) {
            if($nodes -ge 300 -or $clock.Elapsed.TotalSeconds -ge 3){throw 'taskbar_scan_incomplete'}
            $n=$queue.Dequeue();$el=$n.el;$c=$el.Current;$nodes++
            if($c.ControlType -eq [Windows.Automation.ControlType]::Button -and $c.ClassName -ceq 'Taskbar.TaskListButtonAutomationPeer'){$taskButtons.Add($el)}
            if(Test-PocketTaskbarCandidate @{id=$c.AutomationId;kind=$c.ControlType.ProgrammaticName;class=$c.ClassName;enabled=$c.IsEnabled;offscreen=$c.IsOffscreen} $expected){$found.Add($el)}
            $child=$walk.GetFirstChild($el)
            if($child -and $n.depth -ge 20){throw 'taskbar_scan_incomplete'}
            while($child){
                if($nodes+$queue.Count -ge 300 -or $clock.Elapsed.TotalSeconds -ge 3){throw 'taskbar_scan_incomplete'}
                $queue.Enqueue(@{el=$child;depth=$n.depth+1});$child=$walk.GetNextSibling($child)
            }
        }
        if($found.Count -ne 1){throw 'taskbar_button_unavailable'}
        $button=$found[0];$runtime=@($button.GetRuntimeId()) -join ','
        $bounds=$button.Current.BoundingRectangle
        if($bounds.IsEmpty -or $bounds.Width -lt 8 -or $bounds.Height -lt 8 -or $bounds.Width -gt 600 -or $bounds.Height -gt 200){throw 'taskbar_button_unavailable'}
        $point=[Windows.Point]::new([Math]::Floor($bounds.X+$bounds.Width/2),[Math]::Floor($bounds.Y+$bounds.Height/2))
        function Assert-TaskbarHit {
            & $Validate | Out-Null
            $currentOwner=[uint32]0
            [void][PocketTaskbarInput]::GetWindowThreadProcessId($bar,[ref]$currentOwner)
            if($currentOwner -ne $owner -or [PocketTaskbarInput]::FindWindow('Shell_TrayWnd',$null) -ne $bar){throw 'taskbar_button_changed'}
            $now=$button.Current
            if($now.AutomationId -cne $expected -or -not $now.IsEnabled -or $now.IsOffscreen -or
                $now.BoundingRectangle -ne $bounds){throw 'taskbar_button_changed'}
            if(-not [PocketTaskbarInput]::OverTaskbar([int]$point.X,[int]$point.Y,$bar)){throw 'taskbar_button_obscured'}
            $hit=[Windows.Automation.AutomationElement]::FromPoint($point);$matched=$false
            # Windows 11's managed UIA hit test can return only Shell_TrayWnd
            # over a XAML task button. In this exact coarse-provider case,
            # require a unique live app-button rectangle at the physical point.
            $coarse=$hit.Current.NativeWindowHandle -eq $bar.ToInt64() -and $hit.Current.ClassName -ceq 'Shell_TrayWnd'
            for($i=0;$i -lt 10 -and $hit;$i++){
                if((@($hit.GetRuntimeId()) -join ',') -ceq $runtime){$matched=$true;break}
                $hit=$walk.GetParent($hit)
            }
            if(-not $matched -and $coarse){
                $atPoint=@($taskButtons|Where-Object{$v=$_.Current;$v.IsEnabled -and -not $v.IsOffscreen -and $v.BoundingRectangle.Contains($point)})
                $matched=$atPoint.Count -eq 1 -and (@($atPoint[0].GetRuntimeId()) -join ',') -ceq $runtime
            }
            if(-not $matched){throw 'taskbar_button_obscured'}
        }
        Assert-TaskbarHit
        if(-not [PocketTaskbarInput]::KeysUp() -or -not [PocketTaskbarInput]::GetPhysicalCursorPos([ref]$original)){throw 'taskbar_input_changed'}
        $stamp=[PocketTaskbarInput]::InputStamp()
        Start-Sleep -Milliseconds 100
        if(-not [PocketTaskbarInput]::At($original.X,$original.Y) -or [PocketTaskbarInput]::InputStamp() -ne $stamp){throw 'taskbar_input_changed'}
        if(-not [PocketTaskbarInput]::SetPhysicalCursorPos([int]$point.X,[int]$point.Y)){throw 'taskbar_input_unavailable'}
        $moved=$true;$stamp=[PocketTaskbarInput]::InputStamp()
        Assert-TaskbarHit
        $sent=[PocketTaskbarInput]::Click([int]$point.X,[int]$point.Y,$bar,$codex,$stamp)
        if($sent -ne 2){throw 'taskbar_click_unconfirmed'}
        $until=[DateTime]::UtcNow.AddSeconds(2)
        do {
            & $Validate | Out-Null
            if([PocketTaskbarInput]::GetForegroundWindow() -eq $codex){return @{status='foreground_confirmed';clicks=1}}
            Start-Sleep -Milliseconds 100
        } while([DateTime]::UtcNow -lt $until)
        throw 'taskbar_activation_unconfirmed'
    } finally {
        # Never drag a held button or fight a user who moved the pointer.
        $restoreAllowed=$false
        if($moved){try{& $Validate | Out-Null;$restoreAllowed=$true}catch{}}
        if($restoreAllowed -and [PocketTaskbarInput]::At([int]$point.X,[int]$point.Y) -and [PocketTaskbarInput]::KeysUp()){
            [void][PocketTaskbarInput]::SetPhysicalCursorPos($original.X,$original.Y)
        }
        [void][PocketTaskbarInput]::SetThreadDpiAwarenessContext($dpi)
    }
}
