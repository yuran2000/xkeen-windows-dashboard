# release.ps1 - one-shot release for xray-dashboard.
#
# Sets the version ONCE and applies it everywhere: bumps the in-code fallback constant
# _VERSION_FALLBACK in dashboard.py, commits, tags vX.Y.Z, pushes main + tag, and creates
# the GitHub Release. This guarantees the constant and the git tag can never drift.
#
# Why the constant matters: the panel shows its version via `git describe`, but falls back
# to _VERSION_FALLBACK when git is unavailable - which is the ONLY version source for ZIP
# downloads, portable (PyInstaller) builds, and the panel running under SYSTEM without git
# in PATH. If the constant lags the tag, those installs display a stale version.
#
# Usage:
#   .\release.ps1 -Version 1.0.58 -Message "short summary of what changed"

param(
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$Message
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$tag  = "v$Version"

# --- preconditions ---
if ($Version -notmatch '^\d+\.\d+\.\d+$') { Write-Error "Version must be X.Y.Z (e.g. 1.0.58). Got: $Version"; exit 1 }
if (& git -C $repo tag --list $tag) { Write-Error "Tag $tag already exists. Pick the next version."; exit 1 }

# --- 1. bump _VERSION_FALLBACK (UTF-8 without BOM; line endings preserved) ---
$dash = Join-Path $repo "dashboard.py"
$content = [System.IO.File]::ReadAllText($dash)
$pattern = '_VERSION_FALLBACK\s*=\s*"[^"]*"'
if ($content -notmatch $pattern) { Write-Error "_VERSION_FALLBACK not found in dashboard.py"; exit 1 }
$new = [regex]::Replace($content, $pattern, "_VERSION_FALLBACK = `"$Version`"")
[System.IO.File]::WriteAllText($dash, $new, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[1/7] _VERSION_FALLBACK -> $Version"

# --- 2. py_compile gate (never push a broken file; roll back the bump on failure) ---
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m py_compile $dash
if ($LASTEXITCODE -ne 0) { & git -C $repo checkout -- dashboard.py; Write-Error "py_compile failed - bump rolled back."; exit 1 }
Write-Host "[2/7] py_compile OK"

# --- 3. stage all + sanitize gate (block obvious personal data before a public push) ---
& git -C $repo add -A
# exclude this script itself: it literally contains the sanitize pattern (would self-match)
$leak = (& git -C $repo diff --cached -- . ':(exclude)release.ps1') | Select-String -Pattern '192\.168\.(15|35)|46\.36\.217|yu2000\.|111181610'
if ($leak) { & git -C $repo reset | Out-Null; Write-Error "SANITIZE: possible personal data in staged diff:`n$leak"; exit 1 }
Write-Host "[3/7] sanitize CLEAN"

# --- prepare UTF-8 message files ---
# PowerShell 5.1 mangles Cyrillic when passed to git/gh as -m / --title args (cp1251).
# Writing the message to a UTF-8 (no BOM) file and using -F / --notes-file keeps it clean.
$msgFile   = Join-Path $env:TEMP "xkdash_commit_$Version.txt"
$notesFile = Join-Path $env:TEMP "xkdash_notes_$Version.txt"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($msgFile,   "${tag}: $Message", $utf8)
# Release notes = the message + a standard "how to update" footer (kept in a UTF-8 .md so this
# script stays ASCII-only and PowerShell 5.1 can't mangle the Cyrillic footer).
$footer = ""
$footerPath = Join-Path $repo "release_notes_footer.md"
if (Test-Path $footerPath) { $footer = "`n`n" + [System.IO.File]::ReadAllText($footerPath) }
[System.IO.File]::WriteAllText($notesFile, ($Message + $footer), $utf8)

# --- 4. commit ---
& git -C $repo commit -F $msgFile
if ($LASTEXITCODE -ne 0) { Remove-Item $msgFile,$notesFile -ErrorAction SilentlyContinue; Write-Error "commit failed (nothing to commit?)."; exit 1 }
Write-Host "[4/7] commit done"

# --- 5. annotated tag ---
& git -C $repo tag -a $tag -F $msgFile
Write-Host "[5/7] tag $tag"

# --- 6. push main + tag ---
& git -C $repo push origin main
& git -C $repo push origin $tag
Write-Host "[6/7] pushed main + $tag"

# --- 7. GitHub Release (ASCII title = tag; Cyrillic notes via UTF-8 file) ---
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
& $gh release create $tag --repo yuran2000/xkeen-windows-dashboard --title $tag --notes-file $notesFile
Remove-Item $msgFile,$notesFile -ErrorAction SilentlyContinue
Write-Host "[7/7] GitHub Release created: https://github.com/yuran2000/xkeen-windows-dashboard/releases/tag/$tag"
