# Update logic for xray-dashboard.
#
# Why PowerShell: cmd.exe reads .bat files line-by-line and re-reads during
# execution. git pull modifies files on disk -> cmd loses its offset -> reads
# garbage and breaks with errors like:
#   'requirements.txt' is not recognized as an internal or external command
# PowerShell loads the whole .ps1 into memory at start, so changes on disk
# don't affect the current execution. Bulletproof.
#
# update.bat starts this script. Resolve the task and elevation here before changing files.
#
# ASCII only - PowerShell 5.1 on a non-English Windows reads .ps1 in the
# system ANSI codepage (e.g. cp1251 on RU), not UTF-8. Cyrillic in a
# UTF-8-without-BOM .ps1 breaks parsing with cryptic errors.

[CmdletBinding(DefaultParameterSetName = 'Update')]
param(
    [Parameter(ParameterSetName = 'Recovery')][ValidatePattern('^[a-f0-9]{40}$')][string]$RecoveredCommit,
    [Parameter(ParameterSetName = 'Restart')][switch]$RestartOnly
)

function Get-SourceHash([string]$Path) {
    $hash = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','') }
    finally { $stream.Dispose(); $hash.Dispose() }
}

function Get-UpdateTaskState($Folder, [string]$Name) {
    try {
        $task = $Folder.GetTask($Name)
        return 'Present'
    } catch {
        switch ($_.Exception.GetBaseException().HResult) {
            -2147024894 { return 'Missing' }  # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
            -2147024891 { return 'Denied' }   # E_ACCESSDENIED
            default { throw }
        }
    }
}

function Get-UpdateMode([string]$TaskState, [bool]$Elevated) {
    if ($TaskState -eq 'Missing') { return 'Standalone' }
    if ($TaskState -notin @('Present', 'Denied')) { throw 'Unknown scheduler state.' }
    if (-not $Elevated) { return 'Elevate' }
    if ($TaskState -eq 'Denied') { throw 'Access to the dashboard task was denied even as administrator.' }
    return 'Scheduled'
}

function Test-StandalonePanel([int]$ProcessId, [string]$Root) {
    # A venv on Windows can launch a second Python process from the base interpreter.
    $launchers = @((Join-Path $Root '.venv\Scripts\pythonw.exe'), (Join-Path $Root '.venv\Scripts\python.exe'))
    $entry = Join-Path $Root 'dashboard.py'
    $pattern = '(?i)(?:^|\s)"?(?:' + [regex]::Escape($entry) + '|(?:\.\\)?dashboard\.py)"?(?:\s|$)'
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
    if (-not $proc -or $proc.Name -notin @('python.exe','pythonw.exe') -or $proc.CommandLine -notmatch $pattern) { return $false }
    if ($launchers -contains $proc.ExecutablePath) { return $true }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction Stop
    return ($null -ne $parent -and $launchers -contains $parent.ExecutablePath -and
            $parent.CommandLine -match $pattern -and $parent.CreationDate -le $proc.CreationDate)
}

function Get-PanelListeners([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
}

function Test-RunningBuild([int]$Port, [string]$Version, [string]$SourceHash) {
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/login")
    $request.Method = 'HEAD'
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 1800
    try {
        $response = $request.GetResponse()
        try {
            return ([int]$response.StatusCode -eq 200 -and
                    $response.Headers['X-Dashboard-Version'] -ceq $Version -and
                    $response.Headers['X-Dashboard-Source-SHA256'] -ceq $SourceHash)
        } finally { $response.Close() }
    } catch [Net.WebException] {
        if ($_.Exception.Response) { $_.Exception.Response.Close() }
        return $false
    }
}

# Dot-sourcing exposes the preflight and verification functions without performing an update.
if ($MyInvocation.InvocationName -eq '.') { return }

$ErrorActionPreference = 'Continue'
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $InstallDir

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " xray-dashboard update" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ---- Sanity checks ----
if (-not $RestartOnly -and -not (Test-Path ".git")) {
    Write-Host "[ERROR] Not a git repository. Run update only in a folder cloned from GitHub." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
if (-not $RestartOnly -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] git not found in PATH. Install Git for Windows: https://git-scm.com/download/win" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Resolve the configured instance and its launch mode before modifying the checkout.
$python = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { Write-Error "Installation Python missing"; exit 1 }
try {
    $port = 5000
    if (Test-Path -LiteralPath 'config_local.py') {
        $portText = & $python -B -c "import config_local as c; print(getattr(c, 'DASHBOARD_PORT', 5000))"
        if ($LASTEXITCODE -ne 0 -or "$portText" -notmatch '^\d+$') { throw 'Invalid configured dashboard port.' }
        $port = [int]$portText
        if ($port -lt 1 -or $port -gt 65535) { throw 'Invalid configured dashboard port.' }
    }
    $TaskName = if ($Port -eq 5000) { 'XrayDashboard' } else { "XrayDashboard-$Port" }
    $scheduler = New-Object -ComObject Schedule.Service -ErrorAction Stop
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    $mode = Get-UpdateMode (Get-UpdateTaskState $folder $TaskName) ($identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
    if ($mode -eq 'Elevate') {
        $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
        if ($RecoveredCommit) { $arguments += " -RecoveredCommit $RecoveredCommit" }
        if ($RestartOnly) { $arguments += ' -RestartOnly' }
        $elevated = Start-Process -FilePath $shell -Verb RunAs -ArgumentList $arguments -Wait -PassThru -ErrorAction Stop
        exit $elevated.ExitCode
    }
} catch {
    Write-Host "[ERROR] Update preflight failed: $_" -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

# ---- Step 1: verified source update before stopping the running panel ----
$OldReqHash = ""
if (Test-Path "requirements.txt") { $OldReqHash = Get-SourceHash (Join-Path $InstallDir "requirements.txt") }
if ($RecoveredCommit) {
    $currentCommit = & git rev-parse --verify HEAD
    if ($LASTEXITCODE -ne 0 -or $currentCommit -cne $RecoveredCommit) {
        Write-Error 'Recovered checkout changed before restart. The running panel was not stopped.'
        exit 1
    }
    & git diff --quiet $RecoveredCommit --
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Recovered files changed before restart. The running panel was not stopped.'
        exit 1
    }
    Write-Host "[1/5] Recovered source verified" -ForegroundColor Yellow
} elseif ($RestartOnly) {
    Write-Host '[1/5] Using the installed source' -ForegroundColor Yellow
} else {
    Write-Host "[1/5] Updating source with a recovery copy ..." -ForegroundColor Yellow
    & $python (Join-Path $InstallDir "tools\update_checkout.py") --repo $InstallDir
    $checkoutResult = $LASTEXITCODE
    if ($checkoutResult -eq 3) {
        $recovery = Join-Path $InstallDir 'Recover-Update.ps1'
        Write-Host 'The checkout needs recovery. Review the confirmation before changing files.' -ForegroundColor Yellow
        & $recovery
        exit $LASTEXITCODE
    }
    if ($checkoutResult -ne 0) {
        Write-Host "Update stopped. The running panel was not stopped." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# Install dependencies and validate source before stopping the old process.
$NewReqHash = ""
if (Test-Path "requirements.txt") { $NewReqHash = Get-SourceHash (Join-Path $InstallDir "requirements.txt") }
if (($OldReqHash -ne $NewReqHash) -or (Test-Path ".update-needs-deps")) {
    & $python -m pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Dependency installation failed; the running panel was not stopped." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Remove-Item -LiteralPath (Join-Path $InstallDir ".update-needs-deps") -ErrorAction SilentlyContinue
}
& $python -m py_compile dashboard.py
if ($LASTEXITCODE -ne 0) { Write-Error "Source validation failed; the running panel was not stopped."; exit 1 }

# ---- Step 2: stop current instance (only AFTER a successful pull) ----
Write-Host ""
Write-Host "[2/5] Stopping current instance ..." -ForegroundColor Yellow

$versionLine = Select-String -LiteralPath 'dashboard.py' -Pattern '^_VERSION_FALLBACK\s*=\s*"(\d+\.\d+\.\d+)"'
if (@($versionLine).Count -ne 1) { Write-Error 'Updated build version is missing or ambiguous.'; exit 1 }
$expectedVersion = $versionLine.Matches[0].Groups[1].Value
$expectedSourceHash = Get-SourceHash (Join-Path $InstallDir 'dashboard.py')
$oldPids = @(Get-PanelListeners $Port)
try {
    if ($mode -eq 'Scheduled') {
        if ($folder.GetTask($TaskName).State -eq 4 -or $oldPids.Count -gt 0) {
            & schtasks.exe /End /TN $TaskName
            if ($LASTEXITCODE -ne 0) { throw "Could not stop task $TaskName." }
        }
    } else {
        foreach ($processId in $oldPids) {
            if (-not (Test-StandalonePanel $processId $InstallDir)) {
                throw "Port $Port is occupied by a process outside this installation (PID $processId)."
            }
            Stop-Process -Id $processId -Force -ErrorAction Stop
        }
    }
    for ($i=0; $i -lt 16 -and @(Get-PanelListeners $Port).Count; $i++) { Start-Sleep -Milliseconds 500 }
    if (@(Get-PanelListeners $Port).Count) { throw "Port $Port is still in use after stop." }
} catch {
    Write-Host "[FAIL] Dashboard restart stopped: $_" -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host "[3/5] Dependencies validated before restart" -ForegroundColor Gray

# ---- Step 4: check config_local.example.py for new variables ----
Write-Host ""
Write-Host "[4/5] Checking config_local.example.py for new variables ..." -ForegroundColor Yellow
if ((Test-Path "config_local.example.py") -and (Test-Path "config_local.py")) {
    $pyCheck = @'
import re
ex = open('config_local.example.py', encoding='utf-8').read()
cur = open('config_local.py', encoding='utf-8').read()
ex_vars = set(re.findall(r'^([A-Z_]+)\s*=', ex, re.M))
cur_vars = set(re.findall(r'^([A-Z_]+)\s*=', cur, re.M))
missing = ex_vars - cur_vars
if missing:
    print('  New variables in config_local.example.py NOT in your config_local.py:')
    for v in sorted(missing):
        print('    -', v)
    print('  Open config_local.py and add them if you want (optional - old values keep working).')
else:
    print('  config_local.py has all variables from .example - OK.')
'@
    & ".\.venv\Scripts\python.exe" -c $pyCheck
} else {
    Write-Host "  Skipped (one of the files is missing)" -ForegroundColor Gray
}

# ---- Step 5: start ----
Write-Host ""
Write-Host "[5/5] Starting xray-dashboard on port $Port ..." -ForegroundColor Yellow
if ($mode -eq 'Scheduled') {
    & schtasks.exe /Run /TN $TaskName
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not start task $TaskName."; exit 1 }
} else {
    if (-not (Test-Path '.\start-bg.bat')) { Write-Error 'Missing standalone launcher start-bg.bat'; exit 1 }
    & '.\start-bg.bat' -NoPause
    if ($LASTEXITCODE -ne 0) { Write-Error 'Standalone launcher failed'; exit 1 }
}

Write-Host "  Waiting for dashboard v$expectedVersion on port $Port ..."
$ready = $false
for ($i=0; $i -lt 15; $i++) {
    $listeners = @(Get-PanelListeners $Port)
    if ($listeners.Count -gt 0 -and -not @($listeners | Where-Object { $oldPids -contains $_ }).Count -and
        (Test-RunningBuild $Port $expectedVersion $expectedSourceHash)) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    Write-Host "[FAIL] Port $Port did not serve the updated dashboard v$expectedVersion from a new process." -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}
Write-Host "  [OK] Dashboard running on http://localhost:$Port (v$expectedVersion)" -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Update complete!" -ForegroundColor Cyan
Write-Host " This window can be closed - dashboard runs in background." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
if (-not $RecoveredCommit -and -not [Console]::IsInputRedirected) { Read-Host "Press Enter to close" }
exit 0
