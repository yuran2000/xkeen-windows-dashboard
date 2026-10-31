# One-time recovery for an installation using the previous repository history.
param([string]$InstallDir = $PSScriptRoot)
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
    & git fetch origin refs/heads/main
    if ($LASTEXITCODE -ne 0) { throw $messages.fetch }
    $target = & git rev-parse --verify 'FETCH_HEAD^{commit}'
    if ($LASTEXITCODE -ne 0) { throw $messages.target }
    $code = & git show "${target}:tools/update_checkout.py"
    if ($LASTEXITCODE -ne 0) { throw $messages.helper }
    $helper = Join-Path $env:TEMP ('xkeen-recovery-' + [guid]::NewGuid().ToString('N') + '.py')
    [IO.File]::WriteAllText($helper, ($code -join "`n"), (New-Object Text.UTF8Encoding($false)))
    & $python $helper --repo $InstallDir --target $target --repair
    if ($LASTEXITCODE -ne 0) { throw $messages.failed }
    Write-Host $messages.success -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
