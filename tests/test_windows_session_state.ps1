$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot '..\scripts\windows-session-state.cs')
if(-not [PocketWindowsSession]::AllowsInput($true,1,1,1,0,1)){throw 'Known unlocked session refused'}
foreach($case in @(
    @($true,1,1,1,0,0), @($true,1,1,1,0,-1), @($true,1,1,1,4,1),
    @($true,1,2,1,0,1), @($true,2,1,1,0,1), @($false,1,1,1,0,1),
    @($true,1,0,0,0,1)
)) {
    if([PocketWindowsSession]::AllowsInput($case[0],$case[1],$case[2],$case[3],$case[4],$case[5])){
        throw 'Unsafe or unknown session accepted'
    }
}
$level=[PocketWindowsSession].GetNestedType('Level1',[Reflection.BindingFlags]::NonPublic)
$outer=[PocketWindowsSession].GetNestedType('InfoEx',[Reflection.BindingFlags]::NonPublic)
$sizeOf=[Runtime.InteropServices.Marshal].GetMethod('SizeOf',[type[]]@([type]))
if($sizeOf.Invoke($null,@($level)) -ne 224){throw 'Unexpected WTS level layout'}
if([Runtime.InteropServices.Marshal]::OffsetOf($outer,'Data').ToInt32() -ne 8){throw 'Unexpected WTS union alignment'}
if($sizeOf.Invoke($null,@($outer)) -ne 232){throw 'Unexpected WTS buffer size'}
@{ok=$true;cases=11;readOnly=$true} | ConvertTo-Json -Compress
