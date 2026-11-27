"""Build the Windows x64 portable application and independent updater."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import update_release as release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Build the Windows package on Windows.")
    if args.commit and not release.COMMIT.fullmatch(args.commit):
        parser.error("Use the verified release commit.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # PyInstaller expands each work path and generates long icon/cache names.
    # Keep those names independent of the chosen checkout and output paths.
    with tempfile.TemporaryDirectory(prefix="xkd-") as temp:
        build_portable(output, Path(temp), args.commit)


def build_portable(output, temporary, commit):
    tag = release.source_version(ROOT / "dashboard.py")
    build = temporary / "build"
    dist = temporary / "dist" if commit else output / "dist"
    build.mkdir()
    manifest = build / "release-build.json"
    manifest.write_text(json.dumps({"tag": tag, "commit": commit or
                                    release.file_hash(ROOT / "dashboard.py")}), encoding="utf-8")

    def pyinstaller(name, source, mode, dest, options):
        workpath = build / name
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", mode,
                        "--name", name, "--workpath", str(workpath), "--distpath", str(dest),
                        "--specpath", str(build), "--paths", str(ROOT), *options, str(ROOT / source)],
                       cwd=ROOT, check=True)

    data = [
        ("icons8-favicon-64.png", "."), ("config_local.example.py", "."),
        ("portable/config.ini.template", "."), ("bootstrap", "bootstrap"),
        ("tools/update_windows.ps1", "tools"),
    ]
    resources = [option for path, directory in data
                 for option in ("--add-data", f"{ROOT / path};{directory}")]
    pyinstaller("xray-dashboard", "dashboard.py", "--onedir", dist, [
        "--icon", str(ROOT / "icons8-favicon-64.png"),
        *resources, "--add-data", f"{manifest};.",
        "--collect-all", "qrcode", "--collect-all", "flask", "--collect-all", "jinja2",
        "--hidden-import", "psutil", "--hidden-import", "requests", "--hidden-import", "socks",
    ])
    app = dist / "xray-dashboard"
    pyinstaller("dashboard-updater", "dashboard_update.py", "--onefile", app, [])
    release.portable_arch(app / "xray-dashboard.exe")
    release.portable_arch(app / "dashboard-updater.exe")
    if commit:
        if release.portable_info(app, tag) != commit:
            raise RuntimeError("Packaged build identity does not match the tag.")
        archive = output / f"xray-dashboard-{tag}-portable-win-x64.zip"
        pending_archive = archive.with_suffix(".part")
        with ZipFile(pending_archive, "w", ZIP_DEFLATED, compresslevel=6) as bundle:
            for item in sorted(app.rglob("*")):
                if not item.is_dir():
                    bundle.write(item, "xray-dashboard/" + item.relative_to(app).as_posix())
        os.replace(pending_archive, archive)
        print(archive)
    else:
        print(app)


if __name__ == "__main__":
    main()
