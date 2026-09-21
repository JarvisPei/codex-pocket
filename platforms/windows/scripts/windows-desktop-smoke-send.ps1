# Development-only, fixed-draft smoke test. Not a general send adapter.
# This script never writes a draft, navigates, changes focus or presses keys.
param([switch]$DefinitionsOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Test-PocketSmokeDraft([string]$Value) {
    # Keep source ASCII for Windows PowerShell 5.1 without a BOM.
    $expected = 'Pocket Windows ' + [char]0x6d4b + [char]0x8bd5
    return $Value -ceq $expected
}

function Test-PocketSendLabel([string]$Name) {
    return $Name -cmatch '\A(Send|Send message|Submit|\u53d1\u9001|\u53d1\u9001\u6d88\u606f|\u63d0\u4ea4|\u50b3\u9001|\u50b3\u9001\u8a0a\u606f|\u767c\u9001)\z'
}

if ($DefinitionsOnly) { return }
$outcome = @{ readOnly=$false; action='smoke-send'; status='refused'; reason='preflight_failed' }
try {
    if ((Get-Process -Id $PID).SessionId -eq 0) { throw 'interactive_session_required' }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -Path (Join-Path $PSScriptRoot 'windows-session-state.cs')
    Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class PocketSmokeDesktop {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
'@
    if (-not [PocketWindowsSession]::IsUnlocked()) { throw 'desktop_locked_or_unavailable' }
    $foreground = [PocketSmokeDesktop]::GetForegroundWindow()
    if ($foreground -eq [IntPtr]::Zero) { throw 'no_foreground_window' }
    $window = [Windows.Automation.AutomationElement]::FromHandle($foreground)
    $process = Get-Process -Id $window.Current.ProcessId
    $packages = @(Get-AppxPackage -Name OpenAI.Codex)
    $verified = $false
    foreach ($package in $packages) {
        if ($process.Path -and $process.Path.StartsWith($package.InstallLocation + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $verified = $true
        }
    }
    if (-not $verified -or $process.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'codex_not_foreground' }
    $walker = [Windows.Automation.TreeWalker]::RawViewWalker
    $queue = [Collections.Queue]::new()
    $queue.Enqueue(@{element=$window; parent=-1; depth=0; document=$false; usable=$true})
    $nodes = [Collections.Generic.List[object]]::new()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($queue.Count) {
        if ($clock.Elapsed.TotalSeconds -ge 15 -or $nodes.Count -ge 1200) { throw 'incomplete_scan' }
        $entry = $queue.Dequeue()
        $current = $entry.element.Current
        $index = $nodes.Count
        $document = $entry.document -or $current.ControlType -eq [Windows.Automation.ControlType]::Document
        $usable = $entry.usable -and $current.IsEnabled -and -not $current.IsOffscreen
        $nodes.Add(@{element=$entry.element; parent=$entry.parent; document=$document; usable=$usable})
        $child = $walker.GetFirstChild($entry.element)
        if ($child -and $entry.depth -ge 64) { throw 'incomplete_scan' }
        while ($child) {
            if ($clock.Elapsed.TotalSeconds -ge 15 -or $queue.Count + $nodes.Count -ge 1200) { throw 'incomplete_scan' }
            $queue.Enqueue(@{element=$child; parent=$index; depth=$entry.depth+1; document=$document; usable=$usable})
            $child = $walker.GetNextSibling($child)
        }
    }
    $editors = @($nodes | Where-Object {
        $c = $_.element.Current
        $_.usable -and $_.document -and $c.FrameworkId -eq 'Chrome' -and
            $c.ControlType -eq [Windows.Automation.ControlType]::Edit -and $c.IsKeyboardFocusable
    })
    if ($editors.Count -ne 1) { throw 'ambiguous_editor' }
    $editor = $editors[0]
    $valuePattern = $null
    if (-not $editor.element.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern) -or
        $valuePattern.Current.IsReadOnly) { throw 'editor_not_writable' }
    # Value is compared only in memory, never exported or logged.
    if (-not (Test-PocketSmokeDraft $valuePattern.Current.Value)) { throw 'test_draft_mismatch' }
    $containerIndex = $editor.parent
    if ($containerIndex -lt 0 -or $nodes[$containerIndex].element.Current.ControlType -ne [Windows.Automation.ControlType]::Group) {
        throw 'unverified_composer'
    }
    $buttons = @($nodes | Where-Object {
        $node = $_; $c = $node.element.Current; $near = $false
        for ($i=0; $i -lt 3 -and $node.parent -ge 0; $i++) {
            if ($node.parent -eq $containerIndex) { $near=$true; break }
            $node = $nodes[$node.parent]
        }
        $near -and $_.usable -and $_.document -and $c.FrameworkId -eq 'Chrome' -and
            $c.ControlType -eq [Windows.Automation.ControlType]::Button -and (Test-PocketSendLabel $c.Name)
    })
    if ($buttons.Count -ne 1) { throw 'ambiguous_send_button' }
    $button = $buttons[0].element
    $invoke = $null
    if (-not $button.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) { throw 'invoke_unavailable' }
    # Revalidate live state immediately before the single irreversible action.
    if (-not [PocketWindowsSession]::IsUnlocked() -or [PocketSmokeDesktop]::GetForegroundWindow() -ne $foreground) { throw 'foreground_changed' }
    if (-not $button.Current.IsEnabled -or $button.Current.IsOffscreen -or -not (Test-PocketSendLabel $button.Current.Name)) { throw 'button_changed' }
    $liveParent = $walker.GetParent($editor.element)
    if (-not [Windows.Automation.Automation]::Compare($liveParent, $nodes[$containerIndex].element)) { throw 'editor_moved' }
    if (-not (Test-PocketSmokeDraft $valuePattern.Current.Value)) { throw 'test_draft_changed' }
    # Once invoked, an exception/timeout is uncertain, never a reason to retry.
    $outcome.status='outcome_unknown'; $outcome.reason='invoke_attempted'
    $invoke.Invoke()
    $outcome.status='invoke_requested'; $outcome.reason='verify_in_desktop_history'
} catch {
    # Do not expose provider errors: they can contain draft or conversation text.
    $allowed = @('interactive_session_required','desktop_locked_or_unavailable','no_foreground_window',
        'codex_not_foreground','incomplete_scan','ambiguous_editor','editor_not_writable','test_draft_mismatch',
        'unverified_composer','ambiguous_send_button','invoke_unavailable','foreground_changed','button_changed',
        'editor_moved','test_draft_changed')
    if ($outcome.status -eq 'refused' -and $_.Exception.Message -in $allowed) { $outcome.reason=$_.Exception.Message }
}
$outcome | ConvertTo-Json -Compress
