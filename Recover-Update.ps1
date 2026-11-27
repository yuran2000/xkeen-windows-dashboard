# One-time recovery for an installation using the previous repository history.
param([string]$InstallDir = $PSScriptRoot, [switch]$ElevationAttempt)

function Test-RecoverGitWriteAccess([string]$FetchHead) {
    $directory = Split-Path -Parent $FetchHead
    $probe = Join-Path $directory ('xkeen-write-probe-' + [guid]::NewGuid().ToString('N'))
    try {
        $stream = [IO.FileStream]::new($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::None, 1, [IO.FileOptions]::DeleteOnClose)
        $stream.Dispose()

        if ([IO.File]::Exists($FetchHead)) {
            $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
            $stream = [IO.File]::Open($FetchHead, [IO.FileMode]::Open, [IO.FileAccess]::Write, $share)
            $stream.Dispose()
        }
        return $true
    } catch {
        $cause = $_.Exception.GetBaseException()
        if ($cause -is [UnauthorizedAccessException] -or $cause -is [Security.SecurityException]) {
            return $false
        }
        throw
    }
}

function Test-RecoverGitWriteError([string]$Diagnostic) {
    return $Diagnostic -match '(?im)^(?:error|fatal):[^\r\n]*(?:FETCH_HEAD|\.git[\\/]|cannot lock ref|unable to (?:create|write)|cannot store pack)[^\r\n]*(?:Permission denied|Access is denied)\b' -or
        $Diagnostic -match '(?im)^error: insufficient permission for adding an object to repository database\b'
}

function Test-RecoverAdministrator {
    $identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    return $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-RecoverElevation([string]$ScriptPath, [string]$InstallDir, $Messages, [switch]$Attempted) {
    if ($Attempted -or (Test-RecoverAdministrator)) {
        Write-Host $Messages.permissionDenied -ForegroundColor Red
        return 3
    }

    Write-Host $Messages.elevate -ForegroundColor Yellow
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Double any final backslash before the closing quote for Windows argument parsing.
    $directory = $InstallDir -replace '(\\+)$', '$1$1'
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -InstallDir "{1}" -ElevationAttempt' -f $ScriptPath, $directory
    try {
        $child = Start-Process -FilePath $shell -Verb RunAs -WorkingDirectory $InstallDir -ArgumentList $arguments -Wait -PassThru -ErrorAction Stop
    } catch {
        $cause = $_.Exception.GetBaseException()
        if ($cause -is [ComponentModel.Win32Exception] -and $cause.NativeErrorCode -eq 1223) {
            Write-Host $Messages.elevationCanceled -ForegroundColor Yellow
            return 2
        }
        Write-Host "$($Messages.elevationFailed) $($cause.Message)" -ForegroundColor Red
        return 1
    }

    switch ($child.ExitCode) {
        0 { Write-Host $Messages.success -ForegroundColor Green }
        2 { Write-Host $Messages.canceled -ForegroundColor Yellow }
        3 { Write-Host $Messages.permissionDenied -ForegroundColor Red }
        default { Write-Host $Messages.failed -ForegroundColor Red }
    }
    return [int]$child.ExitCode
}

# Dot-sourcing exposes the preflight functions to the offline Windows tests.
if ($MyInvocation.InvocationName -eq '.') { return }

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$messages = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Recover-Update.messages.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host $messages.title -ForegroundColor Cyan
try {
    Set-Location -LiteralPath $InstallDir
    $InstallDir = (Get-Location).Path
    $origin = & git remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $origin -notmatch '^(https://github\.com/|git@github\.com:)yuran2000/(xkeen-windows-dashboard|xray-dashboard)(\.git)?$') {
        throw $messages.origin
    }
    $python = Join-Path $InstallDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw $messages.python }

    $gitPath = & git rev-parse --git-path FETCH_HEAD
    if ($LASTEXITCODE -ne 0 -or -not $gitPath) { throw $messages.metadata }
    $fetchHead = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($gitPath)
    if (-not (Test-RecoverGitWriteAccess $fetchHead)) {
        exit (Invoke-RecoverElevation $PSCommandPath $InstallDir $messages -Attempted:$ElevationAttempt)
    }

    try {
        # PowerShell 5.1 wraps native stderr in error records when redirected.
        $ErrorActionPreference = 'Continue'
        $fetchOutput = & git fetch origin refs/heads/main 2>&1
        $fetchExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
    }
    foreach ($line in $fetchOutput) { Write-Host ([string]$line) }
    if ($fetchExit -ne 0) {
        $diagnostic = $fetchOutput -join "`n"
        if (Test-RecoverGitWriteError $diagnostic) {
            exit (Invoke-RecoverElevation $PSCommandPath $InstallDir $messages -Attempted:$ElevationAttempt)
        }
        if ($diagnostic -match '(?i)could not resolve host|failed to connect|connection timed out') {
            throw $messages.network
        }
        throw $messages.fetch
    }

    $target = & git rev-parse --verify 'FETCH_HEAD^{commit}'
    if ($LASTEXITCODE -ne 0) { throw $messages.target }
    $code = & git show "${target}:tools/update_checkout.py"
    if ($LASTEXITCODE -ne 0) { throw $messages.helper }
    $helper = Join-Path $env:TEMP ('xkeen-recovery-' + [guid]::NewGuid().ToString('N') + '.py')
    [IO.File]::WriteAllText($helper, ($code -join "`n"), (New-Object Text.UTF8Encoding($false)))
    try {
        & $python $helper --repo $InstallDir --target $target --repair
        $result = $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $helper -ErrorAction SilentlyContinue
    }
    if ($result -eq 2) { exit 2 }
    if ($result -ne 0) { throw $messages.failed }
    Write-Host $messages.success -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
