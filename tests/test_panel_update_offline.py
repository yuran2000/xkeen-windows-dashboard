"""Release identity, Windows archive safety and partial overlay rollback."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_update as D
import update_release as R


class Response(io.BytesIO):
    headers = {"ETag": "fixture"}


def reject(code, call):
    try:
        call()
    except R.UpdateError as exc:
        assert exc.code == code, exc
    else:
        raise AssertionError(f"Expected {code}")


def main():
    payload = b"release content"
    sha = hashlib.sha256(payload).hexdigest()
    metadata = {"id": 10, "tag_name": "v1.10.0", "draft": False, "prerelease": False,
                "body": "- First", "assets": [{
                    "name": "xray-dashboard-v1.10.0.zip", "id": 24, "size": len(payload),
                    "digest": "sha256:" + sha, "state": "uploaded"}]}

    def opening(url, _):
        if url.endswith("/releases/latest"):
            return Response(json.dumps(metadata).encode())
        if url.endswith("/releases?per_page=16"):
            history = [dict(metadata, prerelease=False, body="## Изменения\n- **First**\n## Установка\n- Skip"),
                       dict(metadata, tag_name="v1.10.1", prerelease=True),
                       dict(metadata, tag_name="v1.9.9", draft=True)]
            return Response(json.dumps(history).encode())
        if "/commits/" in url:
            return Response(b'{"sha":"1111111111111111111111111111111111111111"}')
        return Response(payload)

    releases = R.Releases(opening)
    git = releases.offer("v1.9.9", "git")
    assert git["newer"] and D.release_identity(git)["commit"] == "1" * 40
    assert not releases.offer("v1.10.0", "git")["newer"]
    assert not releases.offer("v1.11.0", "git")["newer"]
    assert releases.offer("v1.9.9", "portable-x64")["blocked"] == "package_unavailable"
    assert releases.offer("v1.9.9", "unsupported")["blocked"] == "unsupported_install"
    offer = releases.offer("v1.9.9", "zip")
    assert offer["notes"] == ["First"] and offer["asset"]["sha256"] == sha
    metadata["prerelease"] = True
    reject("invalid_release", lambda: releases.offer("v1.9.9", "zip"))

    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        manager = D.UpdateManager(temp, temp, "1.10.0", 5000, releases=releases)
        expected_history = [{"tag": "v1.10.0", "notes": ["First"]}]
        assert manager.history() == {"releases": expected_history}

        def down(*_):
            raise URLError("offline test")

        restarted = D.UpdateManager(temp, temp, "1.10.0", 5000, releases=R.Releases(down))
        assert restarted.history() == {"releases": expected_history, "error": "github_unavailable"}
        releases.download(offer["asset"], offer["tag"], temp / "valid.download")
        reject("invalid_package", lambda: releases.download(
            dict(offer["asset"], sha256="0" * 64), offer["tag"], temp / "invalid.download"))
        for number, paths in enumerate([
            ["source/../outside"], ["source/CON.txt"], ["source/docs/name:stream"],
            ["source/config_local.py"], ["source/backups/secret"], ["source/docs/end."],
            ["source/DOCS/README", "source/docs/other"], ["source/a.txt", "source/A.txt"],
        ]):
            archive = temp / f"invalid-{number}.zip"
            with ZipFile(archive, "w") as bundle:
                for name in paths:
                    bundle.writestr(name, "fixture")
            reject("invalid_package", lambda: R.extract_package(archive, temp / f"bad-{number}", "zip"))

        root = temp / "install"
        root.mkdir()
        (root / "config_local.py").write_text("saved settings", encoding="utf-8")
        (root / "dashboard.py").write_text("old", encoding="utf-8")
        folder = temp / "job"
        staged = folder / "staged"
        staged.mkdir(parents=True)
        (staged / "dashboard.py").write_text("new", encoding="utf-8")
        (staged / "new-file").write_text("new", encoding="utf-8")
        files = D.Files(root, folder, staged, ["dashboard.py", "new-file"])
        previous = files.prepare()
        files.apply(previous)
        assert (root / "dashboard.py").read_text() == "new"
        files.restore(previous)
        assert (root / "dashboard.py").read_text() == "old"
        assert not (root / "new-file").exists()
        assert (root / "config_local.py").read_text() == "saved settings"

        lock = temp / "worker.lock"
        with D.lock(lock):
            try:
                with D.lock(lock):
                    raise AssertionError("Overlapping workers")
            except D.Busy:
                pass

    print("test_panel_update_offline: OK")


if __name__ == "__main__":
    main()
