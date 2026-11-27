"""Published release metadata and verified Windows update packages."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile

REPOSITORY = "yuran2000/xkeen-windows-dashboard"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
GIT_URL = f"https://github.com/{REPOSITORY}.git"
API = f"https://api.github.com/repos/{REPOSITORY}"
TAG = re.compile(r"v(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_PACKAGE = 400 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024
GITHUB_DOWNLOADS = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com"}
PROTECTED = {".git", ".venv", ".env", ".ssh", "ssh", "backups", "config_local.py", "config.ini",
             "runtime_settings.json", "wg_clients.json", "wg_clients.json.bak", "wg_clients.json.tmp",
             ".update-needs-deps"}
PORTABLE_FILES = {"xray-dashboard.exe", "dashboard-updater.exe", "_internal"}
VERSION_LINE = re.compile(r'^_VERSION_FALLBACK\s*=\s*"(\d+\.\d+\.\d+)"', re.M)
RESERVED_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


class UpdateError(RuntimeError):
    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def version_tuple(tag):
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise UpdateError("invalid_release")
    return tuple(int(part) for part in tag[1:].split("."))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def portable_arch(exe):
    with exe.open("rb") as src:
        if src.read(2) != b"MZ":
            raise UpdateError("unsupported_install")
        src.seek(60)
        offset = struct.unpack("<I", src.read(4))[0]
        src.seek(offset)
        if src.read(4) != b"PE\0\0":
            raise UpdateError("unsupported_install")
        machine = struct.unpack("<H", src.read(2))[0]
    if machine != 0x8664:
        raise UpdateError("unsupported_install")
    return "x64"


def source_version(path):
    versions = VERSION_LINE.findall(path.read_text(encoding="utf-8"))
    if len(versions) != 1:
        raise UpdateError("invalid_package")
    return "v" + versions[0]


def installation_kind(root, frozen):
    if not frozen:
        return "git" if (root / ".git").exists() else "zip"
    try:
        portable_arch(root / "xray-dashboard.exe")
    except (OSError, struct.error, UpdateError):
        return "unsupported"
    return "portable-x64" if (root / "_internal").is_dir() else "unsupported"


class GithubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        location = urlsplit(newurl)
        if (location.scheme != "https" or location.hostname not in GITHUB_DOWNLOADS
                or location.username or location.password or location.port not in (None, 443)):
            raise UpdateError("invalid_package", "Unexpected GitHub download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def github_open(url, extra_headers=None):
    headers = {"User-Agent": "XKeen-Windows-Dashboard", "Accept": "application/vnd.github+json"}
    headers.update(extra_headers or {})
    return build_opener(GithubRedirects()).open(Request(url, headers=headers), timeout=12)


def release_notes(body):
    """Extract the short summary from GitHub's public release description."""
    if not isinstance(body, str):
        return []
    bullets, paragraphs = [], []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            if bullets or paragraphs:
                break
            continue
        if not line or line.startswith(("<", "|", "---", "```")):
            continue
        clean = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line).replace("`", "").replace("**", "")
        if clean.startswith(("- ", "* ")):
            bullets.append(clean[2:][:270])
            if len(bullets) == 5:
                break
        elif not bullets and len(paragraphs) < 2:
            paragraphs.append(clean[:270])
    return bullets or paragraphs


class Releases:
    def __init__(self, opener=github_open):
        self.opener = opener
        self.cached = {}

    def metadata(self, path="/releases/latest"):
        etag, cached = self.cached.get(path, (None, None))
        headers = {"If-None-Match": etag} if etag else {}
        try:
            with self.opener(API + path, headers) as response:
                limit = 2 * 1024 * 1024
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise UpdateError("invalid_release")
                release = json.loads(body)
                self.cached[path] = response.headers.get("ETag"), release
                return release
        except HTTPError as exc:
            if exc.code == 304 and cached is not None:
                return cached
            if exc.code in (403, 429) and (exc.code == 429 or exc.headers.get("X-RateLimit-Remaining") == "0"):
                raise UpdateError("rate_limited") from exc
            raise UpdateError("github_unavailable", str(exc)) from exc
        except (OSError, URLError, ValueError) as exc:
            raise UpdateError("github_unavailable", str(exc)) from exc

    def history(self):
        items = self.metadata("/releases?per_page=16")
        if not isinstance(items, list):
            raise UpdateError("invalid_release")
        entries = []
        for item in items:
            if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not False:
                continue
            tag = item.get("tag_name")
            if isinstance(tag, str) and TAG.fullmatch(tag):
                entries.append({"tag": tag, "notes": release_notes(item.get("body"))})
        return sorted(entries, key=lambda entry: version_tuple(entry["tag"]), reverse=True)[:12]

    def offer(self, running, kind):
        release = self.metadata()
        if not isinstance(release, dict):
            raise UpdateError("invalid_release")
        tag = release.get("tag_name")
        newer = version_tuple(tag) > version_tuple(running)
        if release.get("draft") is not False or release.get("prerelease") is not False:
            raise UpdateError("invalid_release")
        if type(release.get("id")) is not int:
            raise UpdateError("invalid_release")
        result = {"id": release["id"], "tag": tag, "kind": kind, "notes": release_notes(release.get("body")),
                  "newer": newer, "link": RELEASES + "/tag/" + tag}
        if not newer:
            return result
        if kind not in ("git", "zip", "portable-x64"):
            result["blocked"] = "unsupported_install"
            return result
        if kind == "git":
            try:
                with self.opener(API + "/commits/" + tag, {}) as response:
                    sha = json.loads(response.read(1024 * 1024))["sha"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise UpdateError("github_unavailable", str(exc)) from exc
            if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
                raise UpdateError("invalid_release")
            result["commit"] = sha
            return result
        name = (f"xray-dashboard-{tag}.zip" if kind == "zip"
                else f"xray-dashboard-{tag}-portable-win-x64.zip")
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("invalid_release")
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("name") != name or asset.get("state") != "uploaded":
                continue
            size, digest = asset.get("size"), asset.get("digest")
            if (type(asset.get("id")) is int and type(size) is int and 0 < size <= MAX_PACKAGE
                    and isinstance(digest, str) and digest.startswith("sha256:")
                    and SHA256.fullmatch(digest[7:])):
                result["asset"] = {"id": asset["id"], "name": name, "size": size, "sha256": digest[7:]}
                return result
        result["blocked"] = "package_unavailable"
        return result

    def download(self, asset, tag, destination):
        url = f"{RELEASES}/download/{tag}/{asset['name']}"
        total, digest, start = 0, hashlib.sha256(), time.monotonic()
        try:
            with self.opener(url, {"Accept": "application/octet-stream"}) as src, destination.open("xb") as dst:
                while chunk := src.read(256 * 1024):
                    total += len(chunk)
                    if total > asset["size"] or time.monotonic() - start > 600:
                        raise UpdateError("invalid_package")
                    digest.update(chunk)
                    dst.write(chunk)
        except (OSError, URLError) as exc:
            raise UpdateError("download_failed", str(exc)) from exc
        if total != asset["size"] or digest.hexdigest() != asset["sha256"]:
            raise UpdateError("invalid_package")


def protected(path):
    return (any(part.casefold() in PROTECTED for part in path.parts)
            or path.suffix.casefold() in {".pem", ".key", ".crt"}
            or path.name.casefold().startswith("id_"))


def reparse_point(path):
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(entry, "st_file_attributes", 0) & 0x400)


def safe_destination(root, relative):
    path = root
    for part in relative.parts:
        path = path / part
        if reparse_point(path):
            raise UpdateError("unsafe_install", str(relative))
    return path


def extract_package(archive, destination, kind):
    """Extract a repository-owned ZIP with one top-level directory."""
    declared, seen, prefix, total = {}, {}, None, 0
    destination.mkdir()
    with ZipFile(archive) as package:
        if len(package.infolist()) > 25000:
            raise UpdateError("invalid_package")
        for info in package.infolist():
            raw = info.orig_filename.removesuffix("/")
            parts = raw.split("/")
            if (not all(parts) or "\\" in raw or any(
                    part in (".", "..") or part.rstrip(" .") != part or RESERVED_NAME.match(part)
                    or re.search(r'[\x00-\x1f:<>"|?*]', part) for part in parts)):
                raise UpdateError("invalid_package", raw[:100])
            prefix = prefix or parts[0]
            if parts[0] != prefix or (len(parts) == 1 and not info.is_dir()):
                raise UpdateError("invalid_package")
            if len(parts) == 1:
                continue
            path = PurePosixPath(*parts[1:])
            if kind == "portable-x64":
                # DLLs and Python modules under _internal are release-owned.
                if path.parts[0] not in PORTABLE_FILES or (len(path.parts) > 1 and path.parts[0] != "_internal"):
                    raise UpdateError("invalid_package", raw[:100])
            elif protected(path):
                raise UpdateError("invalid_package", raw[:100])
            mode = stat.S_IFMT(info.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                raise UpdateError("invalid_package")
            key = path.as_posix().casefold()
            if key in declared:
                raise UpdateError("invalid_package", raw[:100])
            declared[key] = info.is_dir()
            for parent in (path, *path.parents[:-1]):
                key = parent.as_posix().casefold()
                if seen.setdefault(key, parent) != parent:
                    raise UpdateError("invalid_package", raw[:100])
            total += info.file_size
            if total > MAX_UNPACKED:
                raise UpdateError("invalid_package")
            target = destination.joinpath(*path.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)


def portable_info(path, tag):
    if not all((path / name).exists() for name in PORTABLE_FILES):
        raise UpdateError("invalid_package")
    if portable_arch(path / "xray-dashboard.exe") != "x64":
        raise UpdateError("invalid_package")
    info = json.loads((path / "_internal" / "release-build.json").read_text(encoding="utf-8"))
    if info.get("tag") != tag or not COMMIT.fullmatch(info.get("commit", "")):
        raise UpdateError("invalid_package")
    return info["commit"]
