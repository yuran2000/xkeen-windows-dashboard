# Update logic for xray-dashboard.
#
# Why PowerShell: cmd.exe reads .bat files line-by-line and re-reads during
# execution. git pull modifies files on disk -> cmd loses its offset -> reads
# garbage and breaks with errors like:
#   'requirements.txt' is not recognized as an internal or external command
# PowerShell loads the whole .ps1 into memory at start, so changes on disk
# don't affect the current execution. Bulletproof.
#
# update.bat is a thin wrapper that handles UAC elevation if needed (for
# schtasks /End and /Run) and then runs this script. Almost all the logic
# lives here.
#
# ASCII only - PowerShell 5.1 on a non-English Windows reads .ps1 in the
# system ANSI codepage (e.g. cp1251 on RU), not UTF-8. Cyrillic in a
# UTF-8-without-BOM .ps1 breaks parsing with cryptic errors.

function Get-SourceHash([string]$Path) {
    $hash = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','') }
    finally { $stream.Dispose(); $hash.Dispose() }
}

$ErrorActionPreference = 'Continue'
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $InstallDir

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " xray-dashboard update" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ---- Sanity checks ----
if (-not (Test-Path ".git")) {
    Write-Host "[ERROR] Not a git repository. Run update only in a folder cloned from GitHub." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] git not found in PATH. Install Git for Windows: https://git-scm.com/download/win" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ---- Read DASHBOARD_PORT from config_local.py (default 5000) ----
$Port = 5000
$ConfigFile = Join-Path $InstallDir "config_local.py"
if (Test-Path $ConfigFile) {
    $portLine = Select-String -Path $ConfigFile -Pattern '^DASHBOARD_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($portLine -and $portLine.Matches[0].Groups[1].Value) {
        $Port = [int]$portLine.Matches[0].Groups[1].Value
    }
}
$TaskName = if ($Port -eq 5000) { "XrayDashboard" } else { "XrayDashboard-$Port" }
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

# ---- Step 1: verified source update before stopping the running panel ----
$OldReqHash = ""
if (Test-Path "requirements.txt") { $OldReqHash = (Get-SourceHash (Join-Path $InstallDir "requirements.txt")) }
$python = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { Write-Error "Installation Python missing"; exit 1 }
Write-Host "[1/5] Updating source with a recovery copy ..." -ForegroundColor Yellow
& $python (Join-Path $InstallDir "tools\update_checkout.py") --repo $InstallDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "Update stopped. The running panel was not stopped." -ForegroundColor Red
    Write-Host "For a previous repository history or local changes, run Recover-Update.bat once."
    Read-Host "Press Enter to exit"
    exit 1
}

$NewReqHash = ""
if (Test-Path "requirements.txt") {
    $NewReqHash = (Get-SourceHash (Join-Path $InstallDir "requirements.txt"))
}

# Install dependencies and validate source before stopping the old process.
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

# Remember the OLD pythonw PID so we can verify restart actually happened
$oldPids = @()
$oldListeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($oldListeners) {
    $oldPids = $oldListeners.OwningProcess | Sort-Object -Unique
    Write-Host "  Current process on :$Port -> PID(s): $($oldPids -join ', ')"
}

if ($task) {
    schtasks /End /TN $TaskName 2>&1 | Out-Null
    Write-Host "  Sent schtasks /End to task: $TaskName"
    Start-Sleep -Seconds 3
    # Verify task actually stopped - if Task Scheduler auto-restarted, force kill
    $stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($stillListening) {
        Write-Host "  [WARN] Port :$Port still has a listener after /End - force-killing." -ForegroundColor Yellow
        $stillListening.OwningProcess | Sort-Object -Unique | ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }
} else {
    if ($oldPids) {
        $oldPids | ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
            Write-Host "  Stopped PID $_"
        }
        Start-Sleep -Seconds 2
    } else {
        Write-Host "  Not running (no task, nothing listening on :$Port)"
    }
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
if ($task) {
    schtasks /Run /TN $TaskName 2>&1 | Out-Null
    Write-Host "  Started Task Scheduler task: $TaskName"
} else {
    if (Test-Path ".\start-bg.bat") {
        & ".\start-bg.bat"
        Write-Host "  Started via start-bg.bat (no Task Scheduler task)"
    } else {
        Write-Host "  [WARN] No Task Scheduler task and no start-bg.bat - cannot auto-start." -ForegroundColor Yellow
    }
}

Write-Host "  Waiting for dashboard to come up (up to ~30s) ..."
$listen = $null
for ($i = 0; $i -lt 15; $i++) {
    $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listen) { break }
    Start-Sleep -Seconds 2
}
if ($listen) {
    $newPids = $listen.OwningProcess | Sort-Object -Unique
    Write-Host "  Process on :$Port now -> PID(s): $($newPids -join ', ')"
    # Compare to old PIDs - if same, process did NOT restart with new code
    $samePids = $true
    if (-not $oldPids -or $oldPids.Count -eq 0) { $samePids = $false }
    else {
        foreach ($p in $newPids) { if ($oldPids -notcontains $p) { $samePids = $false; break } }
    }
    if ($samePids -and $oldPids -and $oldPids.Count -gt 0) {
        Write-Host "  [FAIL] PIDs unchanged - pythonw was NOT actually restarted." -ForegroundColor Yellow
        Write-Host "         Browser will still show old version. Try running Restart-Clean.bat" -ForegroundColor Yellow
        Write-Host "         (right-click -> Run as administrator) to force-restart." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    } else {
        Write-Host "  [OK] Dashboard running on http://localhost:$Port" -ForegroundColor Green
        Write-Host "       (do Ctrl+F5 in browser to force HTML cache reload)" -ForegroundColor Gray
    }
} else {
    Write-Host "  [FAIL] Not listening on :$Port - try start.bat (in a cmd window) to see errors" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Update complete!" -ForegroundColor Cyan
Write-Host " This window can be closed - dashboard runs in background." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to close"
