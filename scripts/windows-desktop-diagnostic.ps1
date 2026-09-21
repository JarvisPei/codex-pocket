param(
    [ValidateSet('Control', 'Raw')][string]$View = 'Raw',
    [string]$OutputPath,
    [switch]$DefinitionsOnly
)
# Read-only UIA probe. Run in the signed-in desktop, not an SSH/service session.
# No input text is exported: editor values contribute shape metadata only.
# No conversation text, screenshots, clicks, or keyboard injection.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Get-PocketActionLabel([string]$Kind, [string]$Name, [bool]$InDocument) {
    # Caption buttons (e.g. window Restore) are never task actions. Even matches
    # within a document are only candidates, not authorization to invoke them.
    if ($InDocument -and $Kind -eq 'ControlType.Button' -and $Name -match
        '\A(Send|Send message|Submit|Stop|Stop generating|Stop response|Resume|Continue|\u53d1\u9001|\u53d1\u9001\u6d88\u606f|\u63d0\u4ea4|\u505c\u6b62|\u505c\u6b62\u751f\u6210|\u505c\u6b62\u54cd\u5e94|\u7ee7\u7eed|\u6062\u590d|\u50b3\u9001|\u50b3\u9001\u8a0a\u606f|\u767c\u9001|\u505c\u6b62\u7522\u751f|\u7e7c\u7e8c)\z') {
        return $Name
    }
    return $null
}

function Get-PocketScanStatus([int]$EditableCount, [int]$ActionCount, [int]$DocumentCount,
    [bool]$Incomplete) {
    if ($Incomplete) { return 'incomplete_scan' }
    if ($EditableCount -or $ActionCount) { return 'candidate_controls_found' }
    if ($DocumentCount) { return 'web_content_found_no_composer' }
    return 'no_composer_controls'
}

function Get-PocketButtonHint([string]$Kind, [string]$Name, [bool]$InDocument) {
    $action = Get-PocketActionLabel $Kind $Name $InDocument
    if ($null -ne $action) { return $action }
    # Negative evidence matters: voice/dictation are not Send or task Stop.
    # Fixed labels only; never return arbitrary provider strings.
    if ($InDocument -and $Kind -eq 'ControlType.Button' -and $Name -match
        '\A(Dictate|Dictation|Start voice|Start voice chat|Start new voice chat|Stop dictation|Transcribe and send|Queue|Steer|\u542c\u5199|\u5f00\u59cb\u8bed\u97f3|\u5f00\u59cb\u65b0\u7684\u8bed\u97f3\u804a\u5929|\u505c\u6b62\u542c\u5199|\u8f6c\u5f55\u5e76\u53d1\u9001|\u52a0\u5165\u961f\u5217|\u8c03\u6574\u65b9\u5411)\z') {
        return $Name
    }
    return $null
}

function Write-PocketDiagnosticReport($Report, [string]$Path) {
    $json = $Report | ConvertTo-Json -Depth 8
    if ($Path) {
        # Direct UTF-8 avoids Windows PowerShell 5.1 decoding a child process's
        # UTF-8 stdout using the parent's legacy console code page.
        $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
        [IO.File]::WriteAllText($resolved, $json, [Text.UTF8Encoding]::new($false))
        Write-Output 'Diagnostic report saved.'
    } else { Write-Output $json }
}

if ($DefinitionsOnly) { return }
$session = (Get-Process -Id $PID).SessionId
$packages = @(Get-AppxPackage -Name OpenAI.Codex)
$processes = @(Get-Process ChatGPT, Codex -ErrorAction SilentlyContinue | Where-Object {
    $processPath = $_.Path
    $matched = $false
    foreach ($package in $packages) {
        if ($processPath -and $processPath.StartsWith(
            $package.InstallLocation + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $matched = $true
        }
    }
    $matched
})
$report = [ordered]@{
    schemaVersion = 4
    view = $View
    readOnly = $true
    sessionId = $session
    desktopSessionIds = @($processes.SessionId | Sort-Object -Unique)
    appVersions = @($packages | ForEach-Object { $_.Version.ToString() })
    status = 'desktop_not_running'
    visited = 0
    truncated = $false
    truncationReasons = @()
    depthLimit = 64
    nodeLimit = 1200
    timeBudgetSeconds = 15
    errors = 0
    editCount = 0
    documentCount = 0
    actionLabelCount = 0
    editableCandidateCount = 0
    controls = @()
}
if ($session -eq 0 -or ($processes.Count -and -not ($processes | Where-Object SessionId -eq $session))) {
    $report.status = 'interactive_session_required'
} elseif ($processes.Count) {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $ids = @($processes | Where-Object SessionId -eq $session | Select-Object -ExpandProperty Id)
    $root = [Windows.Automation.AutomationElement]::RootElement
    # Only top-level windows first; never traverse other applications' descendants.
    $windows = $root.FindAll([Windows.Automation.TreeScope]::Children,
        [Windows.Automation.Condition]::TrueCondition)
    $queue = [Collections.Queue]::new()
    foreach ($window in $windows) {
        if ($window.Current.ProcessId -in $ids) {
            $queue.Enqueue(@{element=$window; depth=0; parent=-1; inDocument=$false})
        }
    }
    $report.status = if ($queue.Count) { 'inspected' } else { 'no_accessible_window' }
    $controls = [Collections.Generic.List[object]]::new()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $limitsHit = [Collections.Generic.HashSet[string]]::new()
    # Control view can hide embedded provider structure. Raw view is still
    # bounded to these application windows, never to the entire desktop tree.
    $walker = if ($View -eq 'Raw') {
        [Windows.Automation.TreeWalker]::RawViewWalker
    } else { [Windows.Automation.TreeWalker]::ControlViewWalker }
    while ($queue.Count -and $report.visited -lt 1200 -and $clock.Elapsed.TotalSeconds -lt 15) {
        $entry = $queue.Dequeue()
        $index = $report.visited
        $report.visited++
        try {
            $element = $entry.element
            $current = $element.Current
            $kind = $current.ControlType.ProgrammaticName
            $inDocument = $entry.inDocument -or $kind -eq 'ControlType.Document'
            if ($kind -eq 'ControlType.Edit') { $report.editCount++ }
            if ($kind -eq 'ControlType.Document') { $report.documentCount++ }
            # Exact allowlist only. Never export arbitrary names or AutomationIds:
            # both may contain a prompt, a task title, or other private content.
            $label = if ($inDocument -and $kind -eq 'ControlType.Button') {
                Get-PocketActionLabel $kind $current.Name $inDocument
            } else { $null }
            if ($null -ne $label) { $report.actionLabelCount++ }
            $nameHint = $null
            $helpHint = $null
            $hasName = $false
            $hasHelp = $false
            if ($inDocument -and $kind -eq 'ControlType.Button') {
                $hasName = -not [string]::IsNullOrEmpty($current.Name)
                $hasHelp = -not [string]::IsNullOrEmpty($current.HelpText)
                $nameHint = Get-PocketButtonHint $kind $current.Name $inDocument
                $helpHint = Get-PocketButtonHint $kind $current.HelpText $inDocument
            }
            $valueReadOnly = $null
            $editorValueShape = $null
            if ($inDocument -and $kind -in @('ControlType.Edit', 'ControlType.Document')) {
                $valuePattern = $null
                if ($element.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
                    $valueReadOnly = $valuePattern.Current.IsReadOnly
                    if ($kind -eq 'ControlType.Edit') {
                        # Export only shape metadata, never draft text or its hash.
                        $editorValue = [string]$valuePattern.Current.Value
                        $textPattern = $null
                        $textLength = $null
                        if ($element.TryGetCurrentPattern([Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)) {
                            $textLength = $textPattern.DocumentRange.GetText(20001).Length
                        }
                        $editorValueShape = @{
                            length=$editorValue.Length; textLength=$textLength
                            equalsName=($editorValue -ceq $current.Name)
                            equalsHelp=($editorValue -ceq $current.HelpText)
                            knownPlaceholder=($editorValue -cmatch '\A\n(?:Do anything|Ask anything|\u968f\u5fc3\u8f93\u5165)\z')
                            whitespace=[string]::IsNullOrWhiteSpace($editorValue)
                            formatOnly=($editorValue -cmatch '\A[\p{Cf}\s]*\z')
                            characterCategories=@($editorValue.ToCharArray() | ForEach-Object { [char]::GetUnicodeCategory($_).ToString() } | Select-Object -Unique)
                        }
                    }
                }
            }
            if ($inDocument -and ($kind -eq 'ControlType.Edit' -or $valueReadOnly -eq $false)) {
                $report.editableCandidateCount++
            }
            $framework = if ($current.FrameworkId -in @('Win32', 'WinForm', 'WPF', 'XAML', 'WinUI', 'Chrome')) {
                $current.FrameworkId
            } else { 'other' }
            $windowClass = if ($current.ClassName -in @('Chrome_WidgetWin_0', 'Chrome_WidgetWin_1',
                'Chrome_RenderWidgetHostHWND', 'WebViewHost', 'Microsoft.UI.Content.DesktopChildSiteBridge',
                'Windows.UI.Composition.DesktopWindowContentBridge')) {
                $current.ClassName
            } else { 'other' }
            $controls.Add([ordered]@{
                index=$index; parent=$entry.parent; depth=$entry.depth
                type=$kind; label=$label; hasAutomationId=[bool]$current.AutomationId
                nameHint=$nameHint; helpHint=$helpHint; hasName=$hasName; hasHelpText=$hasHelp
                enabled=$current.IsEnabled; offscreen=$current.IsOffscreen
                inDocument=$inDocument; keyboardFocusable=$current.IsKeyboardFocusable
                valueReadOnly=$valueReadOnly
                editorValueShape=$editorValueShape
                frameworkHint=$framework; windowClassHint=$windowClass
                patterns=@($element.GetSupportedPatterns() | ForEach-Object ProgrammaticName)
            })
            $child = $walker.GetFirstChild($element)
            if ($entry.depth -lt $report.depthLimit) {
                while ($null -ne $child -and ($queue.Count + $report.visited) -lt 1200 -and $clock.Elapsed.TotalSeconds -lt 15) {
                    $queue.Enqueue(@{element=$child; depth=$entry.depth+1; parent=$index; inDocument=$inDocument})
                    $child = $walker.GetNextSibling($child)
                }
                if ($null -ne $child) {
                    if (($queue.Count + $report.visited) -ge $report.nodeLimit) { [void]$limitsHit.Add('node_limit') }
                    if ($clock.Elapsed.TotalSeconds -ge $report.timeBudgetSeconds) { [void]$limitsHit.Add('time_limit') }
                }
            } elseif ($null -ne $child) { [void]$limitsHit.Add('depth_limit') }
        } catch {
            # Provider exception messages can contain UI content; keep only count.
            $report.errors++
        }
    }
    if ($queue.Count) {
        if ($report.visited -ge $report.nodeLimit) { [void]$limitsHit.Add('node_limit') }
        if ($clock.Elapsed.TotalSeconds -ge $report.timeBudgetSeconds) { [void]$limitsHit.Add('time_limit') }
    }
    $report.truncationReasons = @($limitsHit | Sort-Object)
    $report.truncated = $limitsHit.Count -gt 0
    $report.controls = @($controls.ToArray())
    if ($report.status -eq 'inspected') {
        # Seeing only window chrome is not evidence that sending can work.
        $report.status = Get-PocketScanStatus $report.editableCandidateCount $report.actionLabelCount `
            $report.documentCount ($report.truncated -or $report.errors -gt 0)
    }
}
Write-PocketDiagnosticReport $report $OutputPath
