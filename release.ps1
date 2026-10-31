# Prepare or publish one verified release. PowerShell 5.1, ASCII source.
param(
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$Message,
    [switch]$PrepareOnly,
    [switch]$PublishPrepared,
    [string]$PythonExe
)
function Get-SourceHash([string]$Path) {
    $hash = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','') }
    finally { $stream.Dispose(); $hash.Dispose() }
}

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$tag = "v$Version"
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Version must be X.Y.Z.' }
if ($PrepareOnly -and $PublishPrepared) { throw 'Choose PrepareOnly or PublishPrepared.' }
$email = & git -C $repo config user.email
if ($LASTEXITCODE -ne 0 -or $email -notmatch '@users\.noreply\.github\.com$') { throw 'Configure a public noreply Git identity first.' }
if (-not $PrepareOnly) {
    $origin = & git -C $repo remote get-url origin
    if ($origin -notin @('https://github.com/yuran2000/xkeen-windows-dashboard.git','git@github.com:yuran2000/xkeen-windows-dashboard.git')) {
        throw 'Publication requires the canonical GitHub origin.'
    }
}
if (-not $PythonExe) { $PythonExe = Join-Path $repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Installation Python not found; use -PythonExe with an explicit interpreter.' }
$receiptDir = Join-Path $repo 'backups'
$receiptFile = Join-Path $receiptDir "release-prepared-$Version.json"
$notesFile = Join-Path $receiptDir "release-notes-$Version.md"
$utf8 = New-Object Text.UTF8Encoding($false)

if ($PublishPrepared) {
    if (-not (Test-Path -LiteralPath $receiptFile)) { throw 'Prepared release receipt not found.' }
    $receipt = Get-Content -LiteralPath $receiptFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $head = & git -C $repo rev-parse HEAD
    $tagHead = & git -C $repo rev-parse "${tag}^{commit}"
    if ($LASTEXITCODE -ne 0 -or $head -ne $receipt.head -or $tagHead -ne $head -or $receipt.message -ne $Message) {
        throw 'Prepared release identity changed; publication stopped.'
    }
    if (& git -C $repo status --porcelain) { throw 'Working tree changed after preparation.' }
    if ((Get-SourceHash $notesFile) -ne $receipt.notes_sha256) {
        throw 'Prepared release notes changed.'
    }
    & $PythonExe (Join-Path $repo 'tools\check_public.py')
    if ($LASTEXITCODE -ne 0) { throw 'Public source check failed.' }
} else {
    if (& git -C $repo tag --list $tag) { throw 'Tag already exists; use PublishPrepared for a verified prepared release.' }
    $dash = Join-Path $repo 'dashboard.py'
    $original = [IO.File]::ReadAllText($dash)
    $pattern = '_VERSION_FALLBACK\s*=\s*"[^"]*"'
    if ([regex]::Matches($original,$pattern).Count -ne 1) { throw 'Expected one version constant.' }
    $changed = [regex]::Replace($original,$pattern,"_VERSION_FALLBACK = `"$Version`"")
    [IO.File]::WriteAllText($dash,$changed,$utf8)
    & $PythonExe -m py_compile $dash
    if ($LASTEXITCODE -ne 0) { [IO.File]::WriteAllText($dash,$original,$utf8); throw 'Syntax check failed; version restored.' }
    & git -C $repo add -A
    if ($LASTEXITCODE -ne 0) { throw 'Staging failed.' }
    & $PythonExe (Join-Path $repo 'tools\check_public.py')
    if ($LASTEXITCODE -ne 0) { throw 'Public source check failed; no commit or push performed.' }
    New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null
    $messageFile = Join-Path $receiptDir "release-commit-$Version.txt"
    [IO.File]::WriteAllText($messageFile,"${tag}: $Message",$utf8)
    & git -C $repo commit -F $messageFile
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed.' }
    & git -C $repo tag -a $tag -F $messageFile
    if ($LASTEXITCODE -ne 0) { throw 'Tag creation failed.' }
    $notes = $Message
    $footer = Join-Path $repo 'release_notes_footer.md'
    if (Test-Path -LiteralPath $footer) { $notes += "`n`n" + [IO.File]::ReadAllText($footer) }
    [IO.File]::WriteAllText($notesFile,$notes,$utf8)
    $head = & git -C $repo rev-parse HEAD
    $receipt = @{version=$Version;tag=$tag;head=$head;message=$Message;notes_sha256=(Get-SourceHash $notesFile);published=$false}
    [IO.File]::WriteAllText($receiptFile,($receipt | ConvertTo-Json),$utf8)
}
if ($PrepareOnly) {
    Write-Host "Prepared locally: $tag ($head). No push or GitHub Release was performed." -ForegroundColor Green
    exit 0
}
& git -C $repo push origin main
if ($LASTEXITCODE -ne 0) { throw 'Main push failed; release not created.' }
& git -C $repo push origin $tag
if ($LASTEXITCODE -ne 0) { throw 'Tag push failed; release not created.' }
& gh release create $tag --repo yuran2000/xkeen-windows-dashboard --title $tag --notes-file $notesFile
if ($LASTEXITCODE -ne 0) { throw 'GitHub Release creation failed; check the remote state before retrying.' }
$receipt.published = $true
[IO.File]::WriteAllText($receiptFile,($receipt | ConvertTo-Json),$utf8)
Write-Host "Published: $tag" -ForegroundColor Green
