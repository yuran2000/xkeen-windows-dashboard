param([string]$PythonExe = 'python.exe')
# Run in Windows PowerShell 5.1. These tests do not change any scheduled task.
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'update.ps1')

function Assert-Equal($Actual, $Expected) {
    if ($Actual -cne $Expected) { throw "Expected '$Expected', got '$Actual'." }
}

$folder = New-Object PSObject
$folder | Add-Member ScriptMethod GetTask {
    param($Name)
    switch ($Name) {
        'present' { return [pscustomobject]@{ State = 4 } }
        'missing' { throw [IO.FileNotFoundException]::new() }
        'denied' { throw [UnauthorizedAccessException]::new() }
        default { throw [InvalidOperationException]::new('scheduler unavailable') }
    }
}
Assert-Equal (Get-UpdateTaskState $folder present) 'Present'
Assert-Equal (Get-UpdateTaskState $folder missing) 'Missing'
Assert-Equal (Get-UpdateTaskState $folder denied) 'Denied'
$unknown = $false
try { Get-UpdateTaskState $folder offline } catch { $unknown = $true }
Assert-Equal $unknown $true
foreach ($admin in @($false,$true)) { Assert-Equal (Get-UpdateMode 'Missing' $admin) 'Standalone' }
Assert-Equal (Get-UpdateMode 'Present' $false) 'Elevate'
Assert-Equal (Get-UpdateMode 'Denied' $false) 'Elevate'
Assert-Equal (Get-UpdateMode 'Present' $true) 'Scheduled'
$refused = $false
try { Get-UpdateMode 'Denied' $true } catch { $refused = $true }
Assert-Equal $refused $true

# Exercise the shipped batch launcher from a path containing spaces.
$base = [IO.Path]::GetTempPath()
$tmp = Join-Path $base ('dashboard port test ' + [guid]::NewGuid().ToString('N'))
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
try {
    New-Item -ItemType Directory -Path $tmp -ErrorAction Stop | Out-Null
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'start-bg.bat') -Destination $tmp
    [IO.File]::WriteAllText((Join-Path $tmp 'config_local.py'), "DASHBOARD_PORT=$port", [Text.Encoding]::ASCII)
    & $PythonExe -m venv --without-pip --copies (Join-Path $tmp '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create test environment.' }
    Push-Location -LiteralPath $tmp
    try {
        $output = & cmd.exe /d /c 'start-bg.bat <nul'
        if ($LASTEXITCODE -ne 0 -or ($output -join ' ') -notlike "*localhost:$port*") {
            throw 'Standalone launcher did not detect the configured listener.'
        }
    } finally { Pop-Location }
} finally {
    $listener.Stop()
    if ((Test-Path -LiteralPath $tmp) -and
        [IO.Path]::GetDirectoryName((Resolve-Path -LiteralPath $tmp).Path) -eq $base.TrimEnd('\')) {
        Remove-Item -LiteralPath $tmp -Recurse -Force
    }
}

Write-Host 'test_update_windows: OK'


