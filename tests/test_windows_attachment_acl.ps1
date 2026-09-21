$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$savedLocalAppData=$env:LOCALAPPDATA
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('pocket-acl-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot|Out-Null
$user=[Security.Principal.WindowsIdentity]::GetCurrent().User
$system=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$scriptPath=Join-Path $PSScriptRoot '..\scripts\windows-repair-attachment-acl.ps1'
try {
    # Isolated fixture only. No live Pocket path is passed to the repair script.
    $env:LOCALAPPDATA=$testRoot
    $root=Join-Path $testRoot 'CodexPocket'
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($user);$acl.SetAccessRuleProtection($true,$false)
    foreach($sid in @($user,$system)){
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
    }
    [IO.Directory]::CreateDirectory($root,$acl)|Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'token.dpapi'),'fixture-not-a-real-credential')
    $untouched=Join-Path $root 'unrelated'
    [IO.Directory]::CreateDirectory($untouched)|Out-Null
    $unrelatedAcl=(Get-Acl $untouched).Sddl
    foreach($name in @('uploads','attachment-handoffs')){
        $legacy=[Security.AccessControl.DirectorySecurity]::new()
        $legacy.SetOwner($user);$legacy.SetAccessRuleProtection($true,$false)
        foreach($sid in @($user,$system,[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'),[Security.Principal.SecurityIdentifier]::new('S-1-3-4'))){
            $legacy.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
        }
        $dir=Join-Path $root $name
        [IO.Directory]::CreateDirectory($dir,$legacy)|Out-Null
        [IO.File]::WriteAllText((Join-Path $dir 'fixture.txt'),'unchanged file content')
    }
    $preview=(& $scriptPath|ConvertFrom-Json)
    if(-not $preview.ok -or $preview.entries -ne 4){throw 'Preview failed'}
    function Get-NetTCPConnection {return @{State='Listen'}}
    $refused=$false
    try{& $scriptPath -Apply|Out-Null}catch{$refused=$_.Exception.Message -eq 'Stop the Pocket Bridge before applying repair'}
    if(-not $refused){throw 'Live listener must prevent changes'}
    # Mock only the network check, and only while LOCALAPPDATA is our fixture.
    function Get-NetTCPConnection {if($env:LOCALAPPDATA -cne $testRoot){throw 'Fixture escaped'};return $null}
    $result=(& $scriptPath -Apply|ConvertFrom-Json)
    if(-not $result.ok -or -not $result.credentialUnchanged -or -not (Test-Path -LiteralPath $result.backup)){throw 'Repair evidence missing'}
    foreach($name in @('uploads','attachment-handoffs')){
        $dir=Join-Path $root $name
        if([IO.File]::ReadAllText((Join-Path $dir 'fixture.txt')) -cne 'unchanged file content'){throw 'Content changed'}
        foreach($item in @((Get-Item $dir),(Get-Item (Join-Path $dir 'fixture.txt')))){
            foreach($rule in (Get-Acl $item.FullName).GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
                if(-not $rule.IsInherited -or $rule.IdentityReference.Value -notin @($user.Value,$system.Value)){throw 'Legacy permission retained'}
            }
        }
    }
    if((Get-Acl $untouched).Sddl -cne $unrelatedAcl){throw 'Unrelated ACL changed'}
    $uploads=Join-Path $root 'uploads'
    $bad=Get-Acl $uploads
    $bad.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
    Set-Acl $uploads $bad
    $before=(Get-Acl $uploads).Sddl
    $refused=$false
    try{& $scriptPath -Apply|Out-Null}catch{$refused=$_.Exception.Message -eq 'Unknown or deny ACL; repair refused'}
    if(-not $refused -or (Get-Acl $uploads).Sddl -cne $before){throw 'Unknown ACL must remain untouched'}
    @{ok=$true;scope='isolated ACL fixture';checks=8}|ConvertTo-Json -Compress
} finally {
    $env:LOCALAPPDATA=$savedLocalAppData
    # Keep the small fixture and backup for inspection; never delete live data.
}
