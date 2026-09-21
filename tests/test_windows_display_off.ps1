$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '..\scripts\windows-desktop-send.ps1') -DefinitionsOnly
if(-not (Test-PocketDisplayOffRoute $true $true 0 0 $false $true)){throw 'Known safe route refused'}
foreach($case in @(
    @($false,$true,0,0,$false,$true), @($true,$false,0,0,$false,$true),
    @($true,$true,-1,0,$false,$true), @($true,$true,1,0,$false,$true),
    @($true,$true,2,0,$false,$true), @($true,$true,0,123,$false,$true),
    @($true,$true,0,0,$true,$true), @($true,$true,0,0,$false,$false)
)) {
    if(Test-PocketDisplayOffRoute $case[0] $case[1] $case[2] $case[3] $case[4] $case[5]){throw 'Unsafe display-off route accepted'}
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path (Join-Path $PSScriptRoot '..\scripts\windows-display-state.cs') -ReferencedAssemblies System.Windows.Forms
$source=Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\scripts\windows-desktop-send.ps1')
if($source -notmatch 'AllowDisplayOff -and \$ActionPhase'){throw 'Stop/Resume experiment must remain disabled'}
if($source -notmatch 'targetIdentity -and \(Get-PocketTargetIdentity'){throw 'Identity recheck missing'}
@{ok=$true;cases=11;readOnly=$true}|ConvertTo-Json -Compress
