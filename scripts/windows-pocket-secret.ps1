param([ValidateSet('Initialize', 'Read', 'Check')][string]$Action = 'Check')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Security

# Fixed per-user child only. Never accept an arbitrary ACL target from HTTP/CLI.
if (-not $env:LOCALAPPDATA -or -not [IO.Path]::IsPathRooted($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA must be an absolute per-user directory.'
}
$pocketRoot = Join-Path $env:LOCALAPPDATA 'CodexPocket'
$pocketToken = Join-Path $pocketRoot 'token.dpapi'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $identity.User
$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')

function Assert-NoReparse([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Reparse points are not supported for the Pocket data directory.'
        }
        if ($item -is [IO.FileInfo]) { $item = $item.Directory }
        else { $item = $item.Parent }
    }
}

function Assert-PrivateAcl([string]$Path) {
    Assert-NoReparse $Path
    $acl = Get-Acl -LiteralPath $Path
    # Elevated test runners may create files owned by Administrators. These
    # privileged owners can already take ownership; they are not extra DACL grants.
    $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @($userSid.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        throw 'Pocket data has an unexpected owner.'
    }
    $userAllowed = $false
    foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow') {
            if ($rule.IdentityReference.Value -notin @($userSid.Value, $systemSid.Value)) {
                throw 'Pocket data has an unexpected allowed principal.'
            }
            if ($rule.IdentityReference.Value -eq $userSid.Value) { $userAllowed = $true }
        }
    }
    if (-not $userAllowed) { throw 'Pocket data does not grant the current user access.' }
}

if ($Action -eq 'Initialize' -and -not (Test-Path -LiteralPath $pocketRoot)) {
    Assert-NoReparse $env:LOCALAPPDATA
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetOwner($userSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($userSid, $systemSid)) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    # Windows PowerShell / .NET Framework supports secure directory creation.
    [IO.Directory]::CreateDirectory($pocketRoot, $acl) | Out-Null
}
Assert-PrivateAcl $pocketRoot
# Validate existing descendants instead of silently weakening/rewriting ACLs.
Get-ChildItem -LiteralPath $pocketRoot -Force | ForEach-Object { Assert-PrivateAcl $_.FullName }

if ($Action -eq 'Initialize' -and -not (Test-Path -LiteralPath $pocketToken)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $stream = [IO.FileStream]::new($pocketToken, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try { $stream.Write($encrypted, 0, $encrypted.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}
Assert-PrivateAcl $pocketToken
$encrypted = [IO.File]::ReadAllBytes($pocketToken)
$plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $encrypted, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
if ($plain.Length -ne 32) { throw 'Invalid Pocket credential.' }
if ($Action -eq 'Check') { Write-Output 'Pocket ACL and DPAPI checks passed.' }
else { Write-Output ([Convert]::ToBase64String($plain)) }
