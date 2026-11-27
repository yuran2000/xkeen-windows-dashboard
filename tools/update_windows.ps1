# Process and Task Scheduler operations for one verified installation.
param(
    [Parameter(Mandatory)][ValidateSet('dispatch','inspect','stop','start','remove')][string]$Action,
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][ValidateRange(1,65535)][int]$Port,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{32}$')][string]$Job,
    [switch]$Portable,
    [int]$ExpectedPid,
    [int]$StartedPid,
    [string]$WorkerProgram,
    [string]$WorkerScript
)
$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$panel = if ($Portable) { Join-Path $Root 'xray-dashboard.exe' } else {
    Join-Path $Root '.venv\Scripts\pythonw.exe'
}
$panelScript = Join-Path $Root 'dashboard.py'
$panelTask = if ($Port -eq 5000) { 'XrayDashboard' } else { "XrayDashboard-$Port" }
$workerTask = "$panelTask-Update-$Job"
$exePattern = '(?i)(?:^|\s)"?(?:' + [regex]::Escape($panelScript) + '|(?:\.\\)?dashboard\.py)"?(?:\s|$)'

function Get-OwnedTask {
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    try { $task = $scheduler.GetFolder('\').GetTask($panelTask) }
    catch {
        if ($_.Exception.GetBaseException().HResult -eq -2147024894) { return }
        throw
    }
    $registered = Get-ScheduledTask -TaskName $panelTask -TaskPath '\'
    $actions = @($registered.Actions)
    if ($actions.Count -ne 1 -or $Portable) { throw "Unexpected action count or installation type for $panelTask." }
    $program = [string]$actions[0].Execute
    $directory = [string]$actions[0].WorkingDirectory
    $arguments = ([string]$actions[0].Arguments).Trim('"')
    if (!$program -or [IO.Path]::GetFullPath($program.Trim('"')) -ine $panel) {
        throw "The executable of $panelTask belongs to another installation."
    }
    if (!$directory -or [IO.Path]::GetFullPath($directory.Trim('"')).TrimEnd('\') -ine $Root) {
        throw "The working directory of $panelTask belongs to another installation."
    }
    if ($arguments -notin @('dashboard.py', '.\dashboard.py', $panelScript)) {
        throw "The action of $panelTask does not directly launch dashboard.py."
    }
    $principal = $registered.Principal.UserId
    if ($principal -notin @('SYSTEM','NT AUTHORITY\SYSTEM','S-1-5-18')) {
        $sid = [Security.Principal.NTAccount]::new($principal).Translate([Security.Principal.SecurityIdentifier])
        if ($sid.Value -ne 'S-1-5-18') { throw "Unexpected Windows account for $panelTask." }
    }
    return $true
}

function Get-Listeners {
    Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object LocalPort -eq $Port | Select-Object -ExpandProperty OwningProcess -Unique
}

function Test-OwnedProcess([int]$Id) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$Id"
    if (!$proc) { return $false }
    if ($Portable) { return $proc.ExecutablePath -ieq $panel }
    if ($proc.CommandLine -notmatch $exePattern) { return $false }
    $launchers = @($panel, (Join-Path $Root '.venv\Scripts\python.exe'))
    if ($launchers -contains $proc.ExecutablePath) { return $true }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)"
    return ($null -ne $parent -and $launchers -contains $parent.ExecutablePath -and
            $parent.CommandLine -match $exePattern -and $parent.CreationDate -le $proc.CreationDate)
}

try {
    if ($Action -eq 'remove') {
        Unregister-ScheduledTask -TaskName $workerTask -Confirm:$false
        exit 0
    }
    $task = Get-OwnedTask
    if ($Action -eq 'dispatch') {
        if (!$task) { exit 10 }
        if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') {
            throw 'The panel must run in its registered SYSTEM task to schedule its update.'
        }
        $arguments = '"{0}" --worker "{1}" {2} --scheduled' -f $WorkerScript,$Root,$Job
        $workerAction = New-ScheduledTaskAction -Execute $WorkerProgram -Argument $arguments -WorkingDirectory (Split-Path -Parent $WorkerScript)
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 35)
        Register-ScheduledTask -TaskName $workerTask -Action $workerAction -Principal $principal -Settings $settings | Out-Null
        try { Start-ScheduledTask -TaskName $workerTask }
        catch {
            Unregister-ScheduledTask -TaskName $workerTask -Confirm:$false
            throw
        }
        exit 0
    }

    $listeners = @(Get-Listeners)
    foreach ($listener in $listeners) {
        if (!(Test-OwnedProcess $listener)) { throw "Port $Port belongs to another program." }
    }
    if ($Action -eq 'inspect') {
        if ($listeners.Count -ne 1 -or $listeners[0] -ne $ExpectedPid) {
            throw "The running panel on port $Port could not be identified."
        }
        if ($task -and [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') {
            throw 'Task Scheduler update worker is not running as SYSTEM.'
        }
        if ($task) { 'scheduled' } else { 'standalone' }
    } elseif ($Action -eq 'stop') {
        if ($ExpectedPid -and ($listeners.Count -ne 1 -or $listeners[0] -ne $ExpectedPid)) {
            throw "The listener changed before stopping port $Port."
        }
        if ($task) {
            Stop-ScheduledTask -TaskName $panelTask
        } else {
            $owned = @($listeners)
            if ($StartedPid -and (Test-OwnedProcess $StartedPid)) { $owned += $StartedPid }
            foreach ($idToStop in ($owned | Select-Object -Unique)) {
                Stop-Process -Id $idToStop -ErrorAction SilentlyContinue
            }
        }
        for ($i=0; $i -lt 24 -and @(Get-Listeners).Count; $i++) { Start-Sleep -Milliseconds 250 }
        if (@(Get-Listeners).Count) { throw "Port $Port was not released." }
    } elseif ($Action -eq 'start') {
        if ($listeners.Count) { throw "Port $Port is already occupied." }
        if ($task) {
            Start-ScheduledTask -TaskName $panelTask
            '0'
        } else {
            $start = @{FilePath=$panel; WorkingDirectory=$Root; WindowStyle='Hidden'; PassThru=$true}
            if (!$Portable) { $start.ArgumentList = 'dashboard.py' }
            (Start-Process @start).Id
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
