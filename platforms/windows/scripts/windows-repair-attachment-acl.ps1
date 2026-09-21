# Explicit repair of the two Pocket-owned attachment trees, never credentials or
# workspaces. Preview by default; stop the Bridge before opting into -Apply.
param([switch]$Apply)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
if(-not $env:LOCALAPPDATA -or -not [IO.Path]::IsPathRooted($env:LOCALAPPDATA)){throw 'Invalid local data directory'}
$root=Join-Path $env:LOCALAPPDATA 'CodexPocket'
$userSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$trusted=@($userSid,'S-1-5-18')
$legacy=@($userSid,'S-1-5-18','S-1-5-32-544','S-1-3-4')
function Assert-PlainPath([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    while($null -ne $item){
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse point refused'}
        if($item -is [IO.FileInfo]){$item=$item.Directory}else{$item=$item.Parent}
    }
}
function Read-CheckedAcl([string]$Path,[bool]$IsParent=$false) {
    Assert-PlainPath $Path
    $acl=Get-Acl -LiteralPath $Path
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @($userSid,'S-1-5-18','S-1-5-32-544')){throw 'Unknown owner; repair refused'}
    $allowed=if($IsParent){$trusted}else{$legacy}
    $userFull=$false;$systemFull=$false
    foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if($rule.AccessControlType -ne 'Allow' -or $rule.IdentityReference.Value -notin $allowed){throw 'Unknown or deny ACL; repair refused'}
        $full=($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl
        $inheritable=($rule.InheritanceFlags -band [Security.AccessControl.InheritanceFlags]::ContainerInherit) -and
            ($rule.InheritanceFlags -band [Security.AccessControl.InheritanceFlags]::ObjectInherit) -and
            $rule.PropagationFlags -eq [Security.AccessControl.PropagationFlags]::None
        if($full -and $inheritable -and $rule.IdentityReference.Value -eq $userSid){$userFull=$true}
        if($full -and $inheritable -and $rule.IdentityReference.Value -eq 'S-1-5-18'){$systemFull=$true}
    }
    if($IsParent -and (-not $acl.AreAccessRulesProtected -or -not $userFull -or -not $systemFull)){throw 'Parent ACL is not the expected private inheritable ACL'}
    return $acl
}
$parent=Read-CheckedAcl $root $true
$parentSddl=$parent.Sddl
$token=Join-Path $root 'token.dpapi'
Assert-PlainPath $token
$tokenAcl=(Get-Acl -LiteralPath $token).Sddl
$tokenHash=(Get-FileHash -LiteralPath $token -Algorithm SHA256).Hash
$items=[Collections.Generic.List[object]]::new()
$queue=[Collections.Queue]::new()
foreach($name in @('uploads','attachment-handoffs')){
    $path=Join-Path $root $name
    if(Test-Path -LiteralPath $path){
        Assert-PlainPath $path
        if(-not (Get-Item -LiteralPath $path -Force).PSIsContainer){throw 'Attachment root is not a directory'}
        $queue.Enqueue((Get-Item -LiteralPath $path -Force))
    }
}
while($queue.Count){
    if($items.Count+$queue.Count -gt 10000){throw 'Attachment tree too large for bounded repair'}
    $item=$queue.Dequeue()
    $acl=Read-CheckedAcl $item.FullName
    $items.Add(@{path=$item.FullName;sddl=$acl.Sddl;directory=$item.PSIsContainer})
    if($item.PSIsContainer){
        foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){
            Assert-PlainPath $child.FullName
            $queue.Enqueue($child)
        }
    }
}
if(-not $Apply){@{ok=$true;mode='preview';entries=$items.Count;targets=@('uploads','attachment-handoffs')}|ConvertTo-Json -Compress;return}
if(Get-NetTCPConnection -State Listen -LocalPort 4317 -ErrorAction SilentlyContinue){throw 'Stop the Pocket Bridge before applying repair'}
# Save only ACL metadata under the already-private parent before changing anything.
$backup=Join-Path $root ('attachment-acl-backup-'+[Guid]::NewGuid().ToString('N')+'.json')
@{createdAt=[DateTime]::UtcNow.ToString('o');entries=@($items.ToArray())}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $backup -Encoding UTF8
foreach($entry in $items){
    [void](Read-CheckedAcl $root $true)
    $checked=Read-CheckedAcl $entry.path
    # Persist DACL only. Passing Get-Acl's whole descriptor to Set-Acl can ask
    # for owner/audit writes (SeSecurityPrivilege) in a limited desktop token.
    $access=[Security.AccessControl.AccessControlSections]::Access
    $acl=if($entry.directory){[Security.AccessControl.DirectorySecurity]::new()}else{[Security.AccessControl.FileSecurity]::new()}
    $acl.SetSecurityDescriptorSddlForm($checked.GetSecurityDescriptorSddlForm($access),$access)
    # Root-first traversal makes inherited entries come only from the verified
    # parent. Remove explicit legacy entries; do not grant new principals.
    foreach($rule in @($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]))){$acl.RemoveAccessRuleSpecific($rule)}
    $acl.SetAccessRuleProtection($false,$false)
    if($entry.directory){[IO.Directory]::SetAccessControl($entry.path,$acl)}else{[IO.File]::SetAccessControl($entry.path,$acl)}
    $after=Get-Acl -LiteralPath $entry.path
    if($after.AreAccessRulesProtected){throw 'Inheritance repair not confirmed'}
    $seen=@()
    foreach($rule in $after.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if($rule.AccessControlType -ne 'Allow' -or -not $rule.IsInherited -or $rule.IdentityReference.Value -notin $trusted){throw 'Unexpected repaired ACL'}
        $seen+=$rule.IdentityReference.Value
    }
    if($userSid -notin $seen -or 'S-1-5-18' -notin $seen){throw 'Inherited access missing'}
}
if((Get-Acl -LiteralPath $root).Sddl -cne $parentSddl -or (Get-Acl -LiteralPath $token).Sddl -cne $tokenAcl -or
    (Get-FileHash -LiteralPath $token -Algorithm SHA256).Hash -cne $tokenHash){throw 'Parent or credential unexpectedly changed'}
@{ok=$true;mode='applied';entries=$items.Count;backup=$backup;credentialUnchanged=$true;parentAclUnchanged=$true}|ConvertTo-Json -Compress
