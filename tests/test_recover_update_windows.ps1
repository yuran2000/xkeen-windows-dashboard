param([string]$PythonExe = 'python.exe')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'Recover-Update.ps1')
$messages = Get-Content -LiteralPath (Join-Path $root 'Recover-Update.messages.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$realAdministrator = Test-RecoverAdministrator

function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -cne $Expected) { throw "$($Case): expected '$Expected', got '$Actual'." }
}
function Assert-Text($Output, [string]$Expected, [string]$Unexpected) {
    $text = $Output -join "`n"
    if (-not $text.Contains($Expected)) { throw "Missing '$Expected' in: $text" }
    if ($Unexpected -and $text.Contains($Unexpected)) { throw "Unexpected '$Unexpected' in: $text" }
}

$base = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd('\')
$tmp = Join-Path $base ('xkeen recovery test ! ' + [guid]::NewGuid().ToString('N'))
$oldPath = $env:Path
$oldMode = $env:XKEEN_TEST_FETCH
$oldLog = $env:XKEEN_TEST_LOG
try {
    New-Item -ItemType Directory -Path (Join-Path $tmp '.git') -Force | Out-Null
    $fetchHead = Join-Path $tmp '.git\FETCH_HEAD'
    Assert-Equal (Test-RecoverGitWriteAccess $fetchHead) $true 'New FETCH_HEAD'
    Assert-Equal @(Get-ChildItem -LiteralPath (Split-Path -Parent $fetchHead) -Force).Count 0 'No probe files left'
    [IO.File]::WriteAllText($fetchHead, 'unchanged')
    Assert-Equal (Test-RecoverGitWriteAccess $fetchHead) $true 'Writable FETCH_HEAD'
    (Get-Item -LiteralPath $fetchHead -Force).IsReadOnly = $true
    Assert-Equal (Test-RecoverGitWriteAccess $fetchHead) $false 'Read-only FETCH_HEAD'
    (Get-Item -LiteralPath $fetchHead -Force).IsReadOnly = $false
    Assert-Equal ([IO.File]::ReadAllText($fetchHead)) 'unchanged' 'Probe leaves contents intact'

    Assert-Equal (Test-RecoverGitWriteError "error: cannot open '.git/FETCH_HEAD': Permission denied") $true 'File permission'
    Assert-Equal (Test-RecoverGitWriteError "error: cannot lock ref 'refs/remotes/origin/main': Access is denied") $true 'Ref lock permission'
    Assert-Equal (Test-RecoverGitWriteError 'error: insufficient permission for adding an object to repository database') $true 'Object permission'
    Assert-Equal (Test-RecoverGitWriteError 'fatal: Could not resolve host: github.com') $false 'Network'
    Assert-Equal (Test-RecoverGitWriteError 'git@github.com: Permission denied (publickey).') $false 'Remote authentication'
    Assert-Equal (Test-RecoverGitWriteError "error: cannot lock ref 'refs/remotes/origin/main': File exists") $false 'Existing lock'

    foreach ($name in 'Recover-Update.bat', 'Recover-Update.ps1', 'Recover-Update.messages.json') {
        Copy-Item -LiteralPath (Join-Path $root $name) -Destination $tmp
    }
    $scriptPath = Join-Path $tmp 'Recover-Update.ps1'
    $script:elevationCalls = 0
    $script:mode = 'success'
    $script:fakeAdmin = $false
    function Test-RecoverAdministrator { return $script:fakeAdmin }
    function Start-Process {
        param($FilePath, $Verb, $WorkingDirectory, $ArgumentList, [switch]$Wait, [switch]$PassThru, $ErrorAction)
        $script:elevationCalls++
        Assert-Equal $Verb 'RunAs' 'UAC request verb'
        Assert-Equal $WorkingDirectory $tmp 'UAC working directory'
        $expected = '-File "{0}" -InstallDir "{1}" -ElevationAttempt' -f $scriptPath, $tmp
        Assert-Equal (([string]$ArgumentList).Contains($expected)) $true 'Quoted arguments'
        if ($script:mode -eq 'cancel') { throw [ComponentModel.Win32Exception]::new(1223) }
        return [pscustomobject]@{ExitCode = if ($script:mode -eq 'denied') { 3 } else { 0 }}
    }
    $result = @(Invoke-RecoverElevation $scriptPath $tmp $messages 6>&1)
    Assert-Equal $result[-1] 0 'Elevated success'
    Assert-Text $result $messages.success $messages.network
    $script:mode = 'cancel'
    $result = @(Invoke-RecoverElevation $scriptPath $tmp $messages 6>&1)
    Assert-Equal $result[-1] 2 'Canceled UAC'
    Assert-Text $result $messages.elevationCanceled $messages.network
    $script:mode = 'denied'
    $result = @(Invoke-RecoverElevation $scriptPath $tmp $messages 6>&1)
    Assert-Equal $result[-1] 3 'Denied by elevated child'
    Assert-Text $result $messages.permissionDenied $messages.network
    $result = @(Invoke-RecoverElevation $scriptPath $tmp $messages -Attempted 6>&1)
    Assert-Equal $result[-1] 3 'No second UAC attempt'
    $script:fakeAdmin = $true
    $result = @(Invoke-RecoverElevation $scriptPath $tmp $messages 6>&1)
    Assert-Equal $result[-1] 3 'Already elevated'
    Assert-Equal $script:elevationCalls 3 'No extra UAC requests'
    Remove-Item -LiteralPath Function:\Start-Process

    # Exercise Windows PowerShell 5.1 with an actual exe, including native stderr and exit codes.
    $source = Join-Path $tmp 'GitStub.cs'
    $stub = Join-Path $tmp 'git.exe'
    $gitStub = @'
using System;
using System.IO;

public static class GitStub {
    public static int Main(string[] args) {
        switch (args[0]) {
            case "remote":
                Console.WriteLine("https://github.com/yuran2000/xkeen-windows-dashboard.git");
                return 0;
            case "rev-parse":
                Console.WriteLine(args[1] == "--git-path" ? ".git/FETCH_HEAD" : new string('a', 40));
                return 0;
            case "fetch":
                File.AppendAllText(Environment.GetEnvironmentVariable("XKEEN_TEST_LOG"), "fetched\n");
                switch (Environment.GetEnvironmentVariable("XKEEN_TEST_FETCH")) {
                    case "network":
                        Console.Error.WriteLine("fatal: Could not resolve host: github.com");
                        return 1;
                    case "rights":
                        Console.Error.WriteLine("error: cannot open '.git/FETCH_HEAD': Permission denied");
                        return 1;
                    case "authentication":
                        Console.Error.WriteLine("git@github.com: Permission denied (publickey).");
                        return 1;
                    default:
                        return 0;
                }
            case "show":
                Console.WriteLine("import os, sys");
                Console.WriteLine("print('fixture-helper-ok')");
                Console.WriteLine("sys.exit(2 if os.environ['XKEEN_TEST_FETCH'] == 'cancel-repair' else 0)");
                return 0;
            default:
                return 1;
        }
    }
}
'@
    [IO.File]::WriteAllText($source, $gitStub, [Text.Encoding]::ASCII)
    $compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
    & $compiler /nologo /target:exe "/out:$stub" $source
    Assert-Equal $LASTEXITCODE 0 'Native Git fixture'
    & $PythonExe -m venv --without-pip --copies (Join-Path $tmp '.venv')
    Assert-Equal $LASTEXITCODE 0 'Fixture Python'
    $env:Path = $tmp + ';' + $oldPath
    $env:XKEEN_TEST_LOG = Join-Path $tmp 'fetch.log'
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $updater = @'
param([string]$RecoveredCommit)
if ($RecoveredCommit -cne ('a' * 40)) { exit 10 }
Write-Host 'fixture-updater-called'
if ($env:XKEEN_TEST_FETCH -eq 'restart-failed') { exit 1 }
'@
    [IO.File]::WriteAllText((Join-Path $tmp 'update.ps1'), $updater, [Text.Encoding]::ASCII)

    function Invoke-TestLauncher([string]$Command, [string]$Mode) {
        $env:XKEEN_TEST_FETCH = $Mode
        Push-Location -LiteralPath $tmp
        try {
            $ErrorActionPreference = 'Continue'
            $output = & cmd.exe /d /v:off /c $Command 2>&1
            [pscustomobject]@{ Code = $LASTEXITCODE; Output = $output }
        } finally {
            Pop-Location
        }
    }

    $result = Invoke-TestLauncher 'Recover-Update.bat <nul' 'success'
    Assert-Equal $result.Code 0 'Batch success'
    Assert-Text $result.Output $messages.success $messages.elevate
    Assert-Text $result.Output 'fixture-helper-ok' $messages.network
    Assert-Text $result.Output 'fixture-updater-called' ''
    $result = Invoke-TestLauncher 'Recover-Update.bat <nul' 'restart-failed'
    Assert-Equal $result.Code 4 'Checkout succeeds but restart fails'
    Assert-Text $result.Output $messages.restartFailed $messages.success
    $result = Invoke-TestLauncher 'Recover-Update.bat <nul' 'cancel-repair'
    Assert-Equal $result.Code 2 'Checkout canceled'
    Assert-Text $result.Output 'fixture-helper-ok' 'fixture-updater-called'
    $result = Invoke-TestLauncher 'Recover-Update.bat <nul' 'network'
    Assert-Text $result.Output $messages.network $messages.elevate
    Assert-Equal $result.Code 1 'Batch network failure'
    $result = Invoke-TestLauncher 'Recover-Update.bat <nul' 'authentication'
    Assert-Equal $result.Code 1 'Batch authentication failure'
    Assert-Text $result.Output $messages.fetch $messages.network

    $alreadyAttempted = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File Recover-Update.ps1 -ElevationAttempt <nul'
    $result = Invoke-TestLauncher $alreadyAttempted 'rights'
    Assert-Equal $result.Code 3 'Local Git error after elevation'
    Assert-Text $result.Output $messages.permissionDenied $messages.network
    $count = @(Get-Content -LiteralPath $env:XKEEN_TEST_LOG).Count
    (Get-Item -LiteralPath $fetchHead -Force).IsReadOnly = $true
    $result = Invoke-TestLauncher $alreadyAttempted 'network'
    Assert-Equal $result.Code 3 'Preflight denial after elevation'
    Assert-Text $result.Output $messages.permissionDenied $messages.network

    if (-not $realAdministrator) {
        # The wrapper intercepts the UAC launch in the real script flow.
        $mock = @'
function Start-Process {
    param($FilePath, $Verb, $WorkingDirectory, $ArgumentList, [switch]$Wait, [switch]$PassThru, $ErrorAction)
    if ($Verb -ne 'RunAs' -or -not $Wait -or -not $PassThru) { throw 'Bad UAC request' }
    Write-Host 'fixture-uac-requested'
    [pscustomobject]@{ExitCode = 2}
}
& (Join-Path $PSScriptRoot 'Recover-Update.ps1')
exit $LASTEXITCODE
'@
        [IO.File]::WriteAllText((Join-Path $tmp 'Mock-Uac.ps1'), $mock, [Text.Encoding]::ASCII)
        $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File Mock-Uac.ps1 <nul'
        $result = Invoke-TestLauncher $command 'network'
        Assert-Equal $result.Code 2 'Denied preflight requests elevation'
        Assert-Text $result.Output 'fixture-uac-requested' $messages.network
        Assert-Text $result.Output $messages.elevate $messages.network
        Assert-Equal @(Get-Content -LiteralPath $env:XKEEN_TEST_LOG).Count $count 'No fetch before elevation'
        (Get-Item -LiteralPath $fetchHead -Force).IsReadOnly = $false
        $result = Invoke-TestLauncher $command 'rights'
        Assert-Equal $result.Code 2 'Local Git write error requests elevation'
        Assert-Text $result.Output 'fixture-uac-requested' $messages.network
        $count++
    }
    Assert-Equal @(Get-Content -LiteralPath $env:XKEEN_TEST_LOG).Count $count 'Denied preflight skipped Git fetch'
    Assert-Equal ([IO.File]::ReadAllText($fetchHead)) 'unchanged' 'FETCH_HEAD preserved'
    (Get-Item -LiteralPath $fetchHead -Force).IsReadOnly = $false
} finally {
    $env:Path = $oldPath
    $env:XKEEN_TEST_FETCH = $oldMode
    $env:XKEEN_TEST_LOG = $oldLog
    if ((Test-Path -LiteralPath $tmp) -and
        [IO.Path]::GetDirectoryName((Resolve-Path -LiteralPath $tmp).Path) -eq $base) {
        Remove-Item -LiteralPath $tmp -Recurse -Force
    }
}
Write-Host 'test_recover_update_windows: OK'
