# Registers xray-dashboard as a SYSTEM Task Scheduler task.
# Run as ADMINISTRATOR.
#
# What this does (in order):
#   1. Verifies admin rights and that .venv exists.
#   2. Detects Microsoft Store Python in venv (SYSTEM cannot access user-profile
#      Python). If found, offers to install Python.org Python machine-wide via
#      winget and rebuild venv.
#   3. Detects SSH key in user-profile ~/.ssh/ and offers to copy it into the
#      project folder with SYSTEM-only permissions (OpenSSH under SYSTEM rejects
#      keys it sees as "owned by someone else").
#   4. Registers the Task Scheduler task XrayDashboard (or XrayDashboard-<port>
#      for non-default ports) running pythonw.exe under SYSTEM, AtStartup.
#
# Multi-instance friendly: the script auto-detects its install folder, so you
# can run parallel services for different routers from different folders.
#
# NOTE: kept ASCII-only on purpose. PowerShell 5.1 on a non-English Windows
# reads .ps1 files in the system ANSI codepage (e.g. cp1251), not UTF-8.
# Cyrillic in a UTF-8-without-BOM .ps1 breaks parsing with cryptic errors
# like "The string is missing the terminator". ASCII sidesteps the whole mess.

$ErrorActionPreference = 'Stop'

# Resolve folder where this script lives (= dashboard install dir)
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Must be admin
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] Not running as Administrator." -ForegroundColor Red
    Write-Host "        Right-click install_service.bat -> Run as administrator." -ForegroundColor Red
    exit 1
}

$VenvPython = Join-Path $InstallDir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] venv not found at: $VenvPython" -ForegroundColor Red
    Write-Host "        Run install.bat in this folder first." -ForegroundColor Red
    exit 1
}

# ============================================================
# STEP 1. Detect Microsoft Store Python in venv
# ============================================================
# Symptom on the broken install: pythonw.exe under SYSTEM stays alive but never
# opens its port. Reason: .venv\Scripts\python.exe is a launcher that redirects
# to the real interpreter in C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\
# SYSTEM has no access to that user-profile path, so it cannot spawn Python.

$pyvenvCfg = Join-Path $InstallDir ".venv\pyvenv.cfg"
$msStoreDetected = $false
$msStoreHomeLine = ""
if (Test-Path $pyvenvCfg) {
    $homeLine = Get-Content $pyvenvCfg -ErrorAction SilentlyContinue | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
    if ($homeLine -and $homeLine -like "*WindowsApps*") {
        $msStoreDetected = $true
        $msStoreHomeLine = $homeLine
    }
}

if ($msStoreDetected) {
    Write-Host ""
    Write-Host "[WARN] venv uses Microsoft Store Python." -ForegroundColor Yellow
    Write-Host "       $msStoreHomeLine" -ForegroundColor Gray
    Write-Host "       SYSTEM service CANNOT access user-profile Python." -ForegroundColor Yellow
    Write-Host "       Plan: install Python.org Python 3.12 machine-wide (via winget)" -ForegroundColor Cyan
    Write-Host "             then rebuild .venv with the new system Python." -ForegroundColor Cyan
    Write-Host ""
    $answer = Read-Host "Proceed? (Y/N)"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Aborted. Service cannot run on Microsoft Store Python." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "Installing Python.org Python 3.12 machine-wide..." -ForegroundColor Cyan
    & winget install Python.Python.3.12 --scope machine --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] winget install failed (exit $LASTEXITCODE)." -ForegroundColor Red
        Write-Host "        Try installing manually from https://python.org/downloads/" -ForegroundColor Red
        exit 1
    }

    # Re-read PATH so the new python is visible in this session
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

    # Find system Python (Program Files\PythonNNN or C:\PythonNNN)
    $sysPython = $null
    $candidates = @()
    $candidates += Get-ChildItem "C:\Program Files\Python*\python.exe" -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "C:\Python*\python.exe" -ErrorAction SilentlyContinue
    if ($candidates.Count -gt 0) { $sysPython = ($candidates | Select-Object -First 1).FullName }
    if (-not $sysPython) {
        Write-Host "[ERROR] System-wide Python not found after install." -ForegroundColor Red
        exit 1
    }
    Write-Host "Found system Python: $sysPython" -ForegroundColor Green

    # Stop any running pythonw before removing venv
    Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    # Remove old venv and create new one
    Write-Host "Removing old venv..." -ForegroundColor Cyan
    Remove-Item (Join-Path $InstallDir ".venv") -Recurse -Force

    Write-Host "Creating new venv with system Python..." -ForegroundColor Cyan
    & $sysPython -m venv (Join-Path $InstallDir ".venv")

    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    $newVenvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
    & $newVenvPy -m pip install -r (Join-Path $InstallDir "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] pip install failed (exit $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }

    $VenvPython = Join-Path $InstallDir ".venv\Scripts\pythonw.exe"
    Write-Host "venv rebuilt successfully." -ForegroundColor Green
}

# ============================================================
# STEP 2. Detect SSH key in user profile, offer to copy to project folder
# ============================================================
# Symptom: ssh under SYSTEM rejects user-profile keys with
#   "UNPROTECTED PRIVATE KEY FILE" + "bad permissions" + "Permission denied".
# Reason: OpenSSH under SYSTEM sees the user-profile key as "owned by another
# user" (because SYSTEM is not the file owner) and refuses to use it.
# Fix: copy the key into the project folder where we can set SYSTEM-only ACL.

$userSshDir = Join-Path $env:USERPROFILE ".ssh"
$projectSshDir = Join-Path $InstallDir ".ssh"
$sshKeyCopied = $null
if (Test-Path $userSshDir) {
    # Look for private keys: anything not .pub, not known_hosts, not config
    $userKeys = Get-ChildItem $userSshDir -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -notlike "*.pub" -and
            $_.Name -notlike "known_hosts*" -and
            $_.Name -notlike "config*" -and
            $_.Name -notlike "authorized_keys*" -and
            $_.Length -lt 10240
        }

    foreach ($key in $userKeys) {
        $targetPath = Join-Path $projectSshDir $key.Name
        if (Test-Path $targetPath) { continue }

        Write-Host ""
        Write-Host "[INFO] Found SSH key in user profile: $($key.FullName)" -ForegroundColor Cyan
        Write-Host "       SYSTEM service cannot use it directly (OpenSSH rejects" -ForegroundColor Gray
        Write-Host "       keys owned by another user as 'too open')." -ForegroundColor Gray
        Write-Host "       Plan: copy to $targetPath" -ForegroundColor Cyan
        Write-Host "             with permissions: SYSTEM + Administrators only" -ForegroundColor Cyan
        Write-Host ""
        $ans = Read-Host "Copy this key? (Y/N)"
        if ($ans -notmatch '^[Yy]') { continue }

        if (-not (Test-Path $projectSshDir)) {
            New-Item -Path $projectSshDir -ItemType Directory -Force | Out-Null
        }

        Copy-Item $key.FullName $targetPath -Force
        $pubSrc = "$($key.FullName).pub"
        if (Test-Path $pubSrc) { Copy-Item $pubSrc "$targetPath.pub" -Force }

        & icacls $targetPath /reset 2>&1 | Out-Null
        & icacls $targetPath /inheritance:r 2>&1 | Out-Null
        & icacls $targetPath /grant:r "SYSTEM:(R)" 2>&1 | Out-Null
        & icacls $targetPath /grant:r "Administrators:(R)" 2>&1 | Out-Null

        Write-Host "Copied: $targetPath" -ForegroundColor Green
        $sshKeyCopied = $targetPath
    }
}

# ============================================================
# STEP 3. Register the Task Scheduler task
# ============================================================

# Read DASHBOARD_PORT from config_local.py (default 5000)
$ConfigFile = Join-Path $InstallDir "config_local.py"
$Port = 5000
if (Test-Path $ConfigFile) {
    $portLine = Select-String -Path $ConfigFile -Pattern '^DASHBOARD_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($portLine -and $portLine.Matches[0].Groups[1].Value) {
        $Port = [int]$portLine.Matches[0].Groups[1].Value
    }
}

$TaskName = if ($Port -eq 5000) { "XrayDashboard" } else { "XrayDashboard-$Port" }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Install folder: $InstallDir" -ForegroundColor Gray
Write-Host " Port:           $Port" -ForegroundColor Gray
Write-Host " Task name:      $TaskName" -ForegroundColor Gray
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# If something is already listening on that port (e.g. start-bg.bat) - stop it
# so the new SYSTEM-owned pythonw doesn't end up as a second listener.
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[INFO] Port $Port is already in use - stopping current process(es)..." -ForegroundColor Yellow
    $existing.OwningProcess | Sort-Object -Unique | ForEach-Object {
        Write-Host "       Stopping PID $_" -ForegroundColor Gray
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

Write-Host "Registering task $TaskName..." -ForegroundColor Green

$action = New-ScheduledTaskAction -Execute $VenvPython -Argument "dashboard.py" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "xray-dashboard Flask panel on port $Port (from $InstallDir) - auto-start under SYSTEM" -Force | Out-Null

Write-Host "Starting..." -ForegroundColor Green
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 6

Write-Host "`nTask status:" -ForegroundColor Cyan
Get-ScheduledTask -TaskName $TaskName | Format-List TaskName, State

Write-Host "pythonw processes:" -ForegroundColor Cyan
Get-Process pythonw -IncludeUserName -ErrorAction SilentlyContinue | Select-Object Id, UserName, StartTime | Format-Table

Write-Host "LISTEN on ${Port}:" -ForegroundColor Cyan
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress, OwningProcess | Format-Table

Write-Host "`nDone. Open http://localhost:$Port" -ForegroundColor Green
if ($sshKeyCopied) {
    Write-Host ""
    Write-Host "[ACTION NEEDED] SSH key was copied to:" -ForegroundColor Yellow
    Write-Host "    $sshKeyCopied" -ForegroundColor Yellow
    Write-Host "    Open the panel, go to the 'router connection' section, paste" -ForegroundColor Yellow
    Write-Host "    the above path into the 'SSH key path' field and save." -ForegroundColor Yellow
}
Write-Host "If you run several instances - each gets its own port and its own XrayDashboard-<port> task." -ForegroundColor Gray
Read-Host "Press Enter to close"
