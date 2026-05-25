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

# ---- Step 1: git pull FIRST (with auto-stash if local changes block it) ----
# v2 (2026-05-25): pull BEFORE stopping the instance. If pull fails (no internet /
# GitHub unreachable - the common case), the running dashboard is left UNTOUCHED, so a
# failed update never takes the panel down. We stop+restart only AFTER pull succeeds.
$OldReqHash = ""
if (Test-Path "requirements.txt") {
    $OldReqHash = (Get-FileHash "requirements.txt" -Algorithm MD5).Hash
}

Write-Host "[1/5] git pull ..." -ForegroundColor Yellow

# Try git pull, capture stdout+stderr together so we can detect known errors
$pullOutput = git pull 2>&1 | Out-String
Write-Host $pullOutput

# If git pull failed because of local changes - auto-stash and retry.
$needStash = ($LASTEXITCODE -ne 0) -and ($pullOutput -match 'local changes' -or
                                          $pullOutput -match 'would be overwritten' -or
                                          $pullOutput -match 'Please commit your changes' -or
                                          $pullOutput -match 'commit your changes or stash')

if ($needStash) {
    Write-Host ""
    Write-Host "[INFO] git pull blocked by local changes - auto-stashing them." -ForegroundColor Yellow
    Write-Host "       (You can see them later with: git stash list / git stash show -p)" -ForegroundColor Gray
    $stashMsg = "auto-stash-by-update-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    git stash push -u -m $stashMsg 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] git stash also failed - update aborted. Dashboard left RUNNING (not touched)." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "       Stashed as: $stashMsg" -ForegroundColor Gray
    Write-Host ""
    Write-Host "       Retrying git pull ..." -ForegroundColor Yellow
    git pull
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[ERROR] git pull still failed after stash - update aborted. Dashboard left RUNNING. Check 'git status' manually." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}
elseif ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] git pull failed - update aborted, dashboard left RUNNING (not touched)." -ForegroundColor Red
    Write-Host "  Common causes:" -ForegroundColor Red
    Write-Host "  - No internet or GitHub unreachable" -ForegroundColor Red
    Write-Host "  - Need to re-login: gh auth login" -ForegroundColor Red
    Write-Host "  - Diverged branches / merge conflict (try: git status)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$NewReqHash = ""
if (Test-Path "requirements.txt") {
    $NewReqHash = (Get-FileHash "requirements.txt" -Algorithm MD5).Hash
}

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

# ---- Step 3: pip install if requirements.txt changed ----
Write-Host ""
if ($OldReqHash -ne $NewReqHash) {
    Write-Host "[3/5] requirements.txt changed - reinstalling dependencies ..." -ForegroundColor Yellow
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -ne 0) {
        # Do NOT exit here - a pip failure must not leave the panel stopped.
        # Warn and continue to the start step (panel runs with previous deps - far better than down).
        Write-Host "[WARN] Failed to update dependencies - will start with existing deps anyway." -ForegroundColor Yellow
    } else {
        Write-Host "  OK"
    }
} else {
    Write-Host "[3/5] requirements.txt unchanged - skipping pip install" -ForegroundColor Gray
}

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
        Write-Host "  [WARN] PIDs unchanged - pythonw was NOT actually restarted." -ForegroundColor Yellow
        Write-Host "         Browser will still show old version. Try running Restart-Clean.bat" -ForegroundColor Yellow
        Write-Host "         (right-click -> Run as administrator) to force-restart." -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] Dashboard running on http://localhost:$Port" -ForegroundColor Green
        Write-Host "       (do Ctrl+F5 in browser to force HTML cache reload)" -ForegroundColor Gray
    }
} else {
    Write-Host "  [FAIL] Not listening on :$Port - try start.bat (in a cmd window) to see errors" -ForegroundColor Red
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Update complete!" -ForegroundColor Cyan
Write-Host " This window can be closed - dashboard runs in background." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to close"
