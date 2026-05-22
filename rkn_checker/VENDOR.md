# Vendored: rkn-block-checker

This `rkn_checker/` package is a **vendored copy** of a third-party tool, bundled
directly into this repository (not installed from PyPI).

| | |
|---|---|
| Upstream | https://github.com/MayersScott/rkn-block-checker |
| PyPI | https://pypi.org/project/rkn-block-checker/ |
| Author | Dmitry Vinogradov |
| License | MIT (see `LICENSE` in this folder) |
| Vendored version | **v0.5.0** |
| Vendored commit | `80d522626536d8f9e7240f99d795ff73ee45929c` |
| Vendored on | 2026-05-22 |

## Why vendored instead of `pip install`

Bundling the source gives us a controlled, reproducible version (the upstream is
beta `0.x` — `pip install -U` could silently pull a breaking release) and means a
clone of this repo carries everything needed, no extra install step.

**MIT obligation:** because we redistribute this code, the `LICENSE` file in this
folder (with `Copyright (c) 2026 Dmitry Vinogradov`) must stay alongside it. That
is the only requirement — modifications are allowed, our own code keeps its own
license.

## Runtime dependency

`rkn_checker` needs `requests>=2.31` (already added to the project
`requirements.txt`). For SOCKS proxy support (`proxy_url=...`) it also needs
`PySocks>=1.7.1` — install only if we use the through-outbound comparison feature.

## How to update later

Pick the new tag from the upstream releases page, then re-download the package
files + LICENSE over this folder (PowerShell, run from anywhere):

```powershell
$gh = (Get-Command gh.exe).Source
$repo = 'MayersScott/rkn-block-checker'
$ref  = 'v0.6.0'   # <-- set to the new tag
$dst  = 'D:\Claude\xray-dashboard-github\rkn_checker'
$files = & $gh api "repos/$repo/contents/rkn_checker?ref=$ref" --jq '.[].name'
foreach ($f in $files) {
  $b64 = (& $gh api "repos/$repo/contents/rkn_checker/$f`?ref=$ref" --jq '.content') -join ''
  [IO.File]::WriteAllBytes("$dst\$f", [Convert]::FromBase64String(($b64 -replace '\s','')))
}
$b64 = (& $gh api "repos/$repo/contents/LICENSE`?ref=$ref" --jq '.content') -join ''
[IO.File]::WriteAllBytes("$dst\LICENSE", [Convert]::FromBase64String(($b64 -replace '\s','')))
```

After updating:
1. Bump **version** and **commit** in the table above.
2. `python -m py_compile rkn_checker\*.py` — must exit 0.
3. Skim upstream CHANGELOG for API changes to `check_url` / `CheckResult` (we depend on those).
4. Test locally, then commit.
