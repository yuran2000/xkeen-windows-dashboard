"""Coordinate an authenticated update request and its independent Windows worker."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zipfile import BadZipFile

import update_release as R
from tools import update_checkout

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
PENDING = {"queued", "preparing", "installing", "starting", "rolling_back"}
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")


class Busy(R.UpdateError):
    def __init__(self):
        super().__init__("busy")


@contextmanager
def lock(path):
    with path.open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Busy() from exc
        yield


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for attempt in range(8):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.04)
    finally:
        temp.unlink(missing_ok=True)


def state_dir(root):
    return R.safe_destination(root, PurePosixPath("backups/panel-update"))


def read_history(state):
    try:
        entries = read_json(state / "release-history.json", [])
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)
            and isinstance(entry.get("tag"), str) and R.TAG.fullmatch(entry["tag"])
            and isinstance(entry.get("notes"), list)
            and all(isinstance(note, str) for note in entry["notes"])]


def remember_history(state, entries):
    """Persist only public notes, for use across a restart or a GitHub outage."""
    if not entries:
        return
    try:
        state.mkdir(parents=True, exist_ok=True)
        with lock(state / "history.lock"):
            by_tag = {entry["tag"]: entry for entry in read_history(state)}
            by_tag.update((entry["tag"], entry) for entry in entries)
            recent = sorted(by_tag, key=R.version_tuple, reverse=True)[:12]
            write_json(state / "release-history.json", [by_tag[tag] for tag in recent])
    except (OSError, Busy):
        pass


def run(args, *, cwd, timeout=40, codes=(0,)):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.run([str(arg) for arg in args], cwd=cwd, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, timeout=timeout, creationflags=NO_WINDOW)
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode("utf-8", "replace").strip()
        if proc.returncode not in codes:
            raise R.UpdateError("command_failed", stderr.read().decode("utf-8", "replace")[-6000:] or output[-6000:])
    return proc.returncode, output


def release_identity(offer):
    # Identical across metadata refreshes even when only the description is edited.
    return {key: offer[key] for key in ("id", "tag", "kind", "commit", "asset") if key in offer}


class Windows:
    def __init__(self, root, folder, request):
        self.root = root
        self.folder = folder
        self.request = request
        self.port = request["port"]
        self.portable = request["release"]["kind"] == "portable-x64"
        self.shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"

    def call(self, action, *args, codes=(0,)):
        argv = [self.shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", self.folder / "update_windows.ps1", "-Action", action,
                "-Root", self.root, "-Port", self.port, "-Job", self.folder.name]
        if self.portable:
            argv.append("-Portable")
        return run([*argv, *args], cwd=self.folder, codes=codes)


class UpdateManager:
    def __init__(self, root, resources, version, port, *, releases=None, frozen=False):
        self.root, self.resources = Path(root), Path(resources)
        self.version, self.port = "v" + version, port
        self.frozen = frozen
        self.releases = releases or R.Releases()
        self.kind = R.installation_kind(self.root, frozen)
        self.state = state_dir(self.root)

    def check(self):
        offer = self.releases.offer(self.version, self.kind)
        remember_history(self.state, [{"tag": offer["tag"], "notes": offer["notes"]}])
        if offer["newer"] and os.name != "nt":
            offer["blocked"] = "unsupported_install"
        if offer["newer"] and self.frozen and not (self.root / "dashboard-updater.exe").is_file():
            offer["blocked"] = "bootstrap_required"
        return offer

    def history(self):
        try:
            entries = self.releases.history()
        except R.UpdateError as exc:
            return {"releases": read_history(self.state), "error": exc.code}
        remember_history(self.state, entries)
        return {"releases": entries}

    def status(self):
        active = read_json(self.state / "active.json", {})
        job = active.get("job", "")
        if not JOB_ID.fullmatch(job):
            return {}
        folder = self.state / job
        state = read_json(folder / "status.json", {})
        if state.get("phase") in PENDING and time.time() - state.get("time", 0) > 60:
            try:
                with lock(self.state / "worker.lock"):
                    state = read_json(folder / "status.json", {})
                    if state.get("phase") in PENDING:
                        error = ("interrupted" if (folder / "transaction.json").exists()
                                 else "interrupted_preparation")
                        write_status(folder, "failed", error=error)
                        state = read_json(folder / "status.json", {})
            except Busy:
                pass
        return {"job": job, "tag": active["tag"], **{key: state[key] for key in
                ("phase", "error", "rollback", "time") if key in state}}

    def queue(self, expected):
        if os.name != "nt":
            raise R.UpdateError("unsupported_install")
        self.state.mkdir(parents=True, exist_ok=True)
        with lock(self.state / "request.lock"):
            current = self.status()
            if current.get("phase") in PENDING:
                raise Busy()
            if (current.get("error") == "interrupted" and
                    (self.state / current["job"] / "transaction.json").exists()):
                raise R.UpdateError("interrupted")
            if current.get("rollback") == "failed":
                raise R.UpdateError("interrupted")
            offer = self.check()
            if not offer["newer"] or offer.get("blocked") or expected != release_identity(offer):
                raise R.UpdateError("offer_changed")
            try:
                # The console updater also holds this lock.
                with lock(self.state / "worker.lock"):
                    job = secrets.token_hex(16)
                    folder = self.state / job
                    folder.mkdir()
                    request = {"release": release_identity(offer), "previous": self.version,
                               "port": self.port, "pid": os.getpid()}
                    write_json(folder / "request.json", request)
                    shutil.copy2(self.resources / "tools/update_windows.ps1", folder / "update_windows.ps1")
                    write_status(folder, "queued")
                    write_json(self.state / "active.json", {"job": job, "tag": offer["tag"]})
            except OSError as exc:
                raise R.UpdateError("permission_denied", str(exc)) from exc
            try:
                self.dispatch(folder, request)
            except (OSError, subprocess.SubprocessError, R.UpdateError) as exc:
                log_error(folder, exc)
                write_status(folder, "failed", error="launch_failed")
                raise R.UpdateError("launch_failed") from exc
            return self.status()

    def dispatch(self, folder, request):
        args = ["--worker", self.root, folder.name]
        if self.frozen:
            worker = folder / "dashboard-updater.exe"
            shutil.copy2(self.root / worker.name, worker)
            invocation = [worker, *args]
        else:
            program = Path(sys._base_executable).with_name("python.exe")
            script = folder / "dashboard_update.py"
            for name in (script.name, "update_release.py", "tools/update_checkout.py"):
                dest = folder / name
                dest.parent.mkdir(exist_ok=True)
                shutil.copy2(self.resources / name, dest)
            mode, _ = Windows(self.root, folder, request).call(
                "dispatch", "-WorkerProgram", program, "-WorkerScript", script, codes=(0, 10))
            if mode == 0:
                return
            invocation = [program, script, *args]
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1") if self.frozen else None
        subprocess.Popen([str(arg) for arg in invocation], cwd=folder, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED, env=env)


def write_status(folder, phase, **extra):
    write_json(folder / "status.json", {"phase": phase, "time": time.time(), **extra})


def log_error(folder, exc):
    with (folder / "update.log").open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(exc).__name__}: {exc}\n")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_running(port):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(f"http://127.0.0.1:{port}/login", method="HEAD",
                      headers={"Cache-Control": "no-cache"})
    try:
        with opener.open(request, timeout=1.5) as response:
            headers = response.headers
            if response.status == 200:
                return ("v" + headers.get("X-Dashboard-Version", ""),
                        headers.get("X-Dashboard-Build") or headers.get("X-Dashboard-Source-SHA256", "").lower(),
                        int(headers.get("X-Dashboard-PID", "0")))
    except (OSError, ValueError):
        pass
    return None


def wait_running(port, build, previous_pid, timeout=38):
    expires = time.monotonic() + timeout
    while time.monotonic() < expires:
        current = read_running(port)
        if current and current[:2] == tuple(build) and current[2] not in (0, previous_pid):
            return current[2]
        time.sleep(0.45)
    raise R.UpdateError("health_failed")


def git_executable():
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        program = Path(os.environ.get(var, "C:/Program Files")) / "Git/cmd/git.exe"
        if program.is_file():
            return program
    program = shutil.which("git")
    if not program:
        raise R.UpdateError("git_missing")
    return Path(program)


class Files:
    def __init__(self, root, folder, staged, entries):
        self.root, self.folder, self.staged = root, folder, staged
        self.entries = entries

    def prepare(self):
        previous = {}
        for name in self.entries:
            path = R.safe_destination(self.root, PurePosixPath(name))
            target = self.staged / name
            if path.exists() and (path.is_dir() != target.is_dir() or path.is_file() != target.is_file()):
                raise R.UpdateError("unsafe_install", name)
            if path.is_dir():
                for base, directories, files in os.walk(path):
                    if any(R.reparse_point(Path(base) / item) for item in (*directories, *files)):
                        raise R.UpdateError("unsafe_install", name)
            previous[name] = path.exists()
        return previous

    def apply(self, previous):
        saved = self.folder / "files-before"
        for name in self.entries:
            path = R.safe_destination(self.root, PurePosixPath(name))
            backup = saved / name
            if previous[name]:
                if not path.exists():
                    raise R.UpdateError("unsafe_install", f"File removed during update: {name}")
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, backup)
            elif path.exists():
                raise R.UpdateError("unsafe_install", f"File created during update: {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.staged / name, path)

    def restore(self, previous):
        saved = self.folder / "files-before"
        discarded = self.folder / "files-failed"
        for name, existed in reversed(tuple(previous.items())):
            path = R.safe_destination(self.root, PurePosixPath(name))
            backup = saved / name
            # A present staged file and absent backup means this entry was never switched.
            if not backup.exists() and (existed or (self.staged / name).exists()):
                continue
            if path.exists():
                target = discarded / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
            if backup.exists():
                os.replace(backup, path)


class Worker:
    tag_ref = "refs/xkeen-update/target"

    def __init__(self, root, folder, request, *, releases=None, runtime=None):
        self.root, self.folder, self.request = root, folder, request
        self.offer = request["release"]
        self.kind, self.tag = self.offer["kind"], self.offer["tag"]
        self.port = request["port"]
        self.runtime = runtime or Windows(root, folder, request)
        self.releases = releases or R.Releases()
        self.staged = self.folder / "staged"
        self.transaction = self.folder / "transaction.json"
        self.venv = self.root / ".venv"
        self.python = self.venv / "Scripts/python.exe"
        self.git_program = None

    def git(self, *args, timeout=90, codes=(0,)):
        if self.git_program is None:
            self.git_program = git_executable()
        return run([self.git_program, "-C", self.root, *self.git_options(), "-c", "credential.interactive=never",
                    "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=20", *args],
                   cwd=self.folder, timeout=timeout, codes=codes)

    def git_options(self):
        return ("-c", "safe.directory=" + self.root.as_posix(), "-c", f"core.hooksPath={os.devnull}")

    def prepare_git(self):
        git_dir = R.safe_destination(self.root, PurePosixPath(".git"))
        if not git_dir.is_dir():
            raise R.UpdateError("foreign_checkout")
        _, origin = self.git("remote", "get-url", "origin")
        if origin not in update_checkout.ORIGINS:
            raise R.UpdateError("foreign_checkout")
        _, branch = self.git("symbolic-ref", "--short", "HEAD")
        _, top = self.git("rev-parse", "--show-toplevel")
        if Path(top).resolve() != self.root or branch != "main":
            raise R.UpdateError("foreign_checkout")
        _, dirty = self.git("status", "--porcelain")
        _, old_head = self.git("rev-parse", "HEAD")
        if dirty:
            raise R.UpdateError("recovery_required")
        self.git("fetch", "--no-tags", "--no-write-fetch-head", R.GIT_URL,
                 f"+refs/tags/{self.tag}:{self.tag_ref}")
        _, target = self.git("rev-parse", self.tag_ref + "^{commit}")
        if target != self.offer["commit"]:
            raise R.UpdateError("offer_changed")
        _, tag_object = self.git("rev-parse", self.tag_ref)
        _, local = self.git("rev-parse", "--verify", "refs/tags/" + self.tag, codes=(0, 128))
        if local and local != tag_object:
            raise R.UpdateError("recovery_required")
        rc, _ = self.git("merge-base", "--is-ancestor", old_head, target, codes=(0, 1))
        if rc:
            raise R.UpdateError("recovery_required")
        markers = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
        for marker in markers:
            _, path = self.git("rev-parse", "--git-path", marker)
            if (self.root / path).exists():
                raise R.UpdateError("recovery_required")
        archive = self.folder / "source.zip"
        self.git("archive", "--format=zip", "--prefix=source/", f"--output={archive}", target)
        R.extract_package(archive, self.staged, "zip")
        return {"old_head": old_head, "target": target, "tag_object": tag_object, "previous_tag": local}

    def prepare_dependencies(self):
        if self.kind == "portable-x64":
            return False
        staged = self.staged / "requirements.txt"
        existing = self.root / "requirements.txt"
        if R.file_hash(staged) == R.file_hash(existing) and not (self.root / ".update-needs-deps").exists():
            return False
        wheels = self.folder / "wheels"
        wheels.mkdir()
        run([self.python, "-m", "pip", "--disable-pip-version-check", "download",
             "--only-binary=:all:", "--dest", wheels, "-r", staged], cwd=self.folder, timeout=300)
        return True

    def prepare(self):
        fresh = self.releases.offer(self.request["previous"], self.kind)
        if fresh.get("blocked") or release_identity(fresh) != self.offer or not fresh["newer"]:
            raise R.UpdateError("offer_changed")
        remember_history(self.folder.parent, [{"tag": fresh["tag"], "notes": fresh["notes"]}])
        self.runtime.call("inspect", "-ExpectedPid", self.request["pid"])
        running = read_running(self.port)
        if not running or running[0] != self.request["previous"] or running[2] != self.request["pid"] or not running[1]:
            raise R.UpdateError("instance_changed")
        if shutil.disk_usage(self.root).free < 128 * 1024 * 1024 + self.offer.get("asset", {}).get("size", 0) * 3:
            raise R.UpdateError("disk_space")
        data = {"previous_build": running[:2]}
        marker = self.root / ".update-needs-deps"
        if self.kind != "portable-x64" and marker.is_file():
            data["dependency_marker"] = marker.read_text(encoding="ascii")
        if self.kind == "git":
            data.update(self.prepare_git())
        else:
            archive = self.folder / "release.zip"
            self.releases.download(self.offer["asset"], self.tag, archive)
            R.extract_package(archive, self.staged, self.kind)
        if self.kind == "portable-x64":
            data["build"] = R.portable_info(self.staged, self.tag)
            entries = sorted(R.PORTABLE_FILES)
        else:
            dashboard = self.staged / "dashboard.py"
            if R.source_version(dashboard) != self.tag:
                raise R.UpdateError("invalid_package")
            compile(dashboard.read_bytes(), "dashboard.py", "exec")
            data["build"] = R.file_hash(dashboard)
            entries = sorted(p.relative_to(self.staged).as_posix() for p in self.staged.rglob("*") if p.is_file())
        if self.kind != "git":
            data["files"] = Files(self.root, self.folder, self.staged, entries).prepare()
        data["deps"] = self.prepare_dependencies()
        return data

    def apply(self, data):
        if self.kind == "git":
            # Reuse the console updater's verified bundle and protected-file checks.
            update_checkout.GIT_EXE = str(self.git_program)
            update_checkout.GIT_OPTIONS = self.git_options()
            update_checkout.apply_update(self.root, data["target"])
        else:
            files = data["files"]
            Files(self.root, self.folder, self.staged, files).apply(files)
        if data["deps"]:
            backup = self.folder / "venv-before"
            if R.reparse_point(self.venv):
                raise R.UpdateError("unsafe_install", ".venv")
            shutil.copytree(self.venv, backup, symlinks=True)
            (self.folder / "venv-ready").touch()
            run([self.python, "-m", "pip", "--disable-pip-version-check", "install", "--no-index",
                 "--find-links", self.folder / "wheels", "-r", self.root / "requirements.txt"],
                cwd=self.root, timeout=300)
            run([self.python, "-m", "pip", "check"], cwd=self.root, timeout=60)
        if self.kind != "portable-x64":
            (self.root / ".update-needs-deps").unlink(missing_ok=True)

    def restore(self, data):
        if self.kind == "git":
            _, head = self.git("rev-parse", "HEAD")
            if head == data["target"]:
                self.git("reset", "--keep", data["old_head"])
                (self.root / ".update-needs-deps").unlink(missing_ok=True)
            elif head != data["old_head"]:
                raise R.UpdateError("instance_changed")
        else:
            files = data["files"]
            Files(self.root, self.folder, self.staged, files).restore(files)
        backup = self.folder / "venv-before"
        if (self.folder / "venv-ready").exists() and backup.exists():
            if self.venv.exists():
                os.replace(self.venv, self.folder / "venv-failed")
            os.replace(backup, self.venv)
        if "dependency_marker" in data:
            (self.root / ".update-needs-deps").write_text(data["dependency_marker"], encoding="ascii")

    def execute(self):
        started_pid = 0
        data = None
        stopped = False
        stopping = False
        try:
            write_status(self.folder, "preparing")
            data = self.prepare()
            write_json(self.transaction, data)
            if self.kind == "git":
                if self.git("rev-parse", "HEAD")[1] != data["old_head"] or self.git("status", "--porcelain")[1]:
                    raise R.UpdateError("recovery_required")
            write_status(self.folder, "installing")
            stopping = True
            self.runtime.call("stop", "-ExpectedPid", self.request["pid"])
            stopped = True
            self.apply(data)
            write_status(self.folder, "starting")
            _, pid = self.runtime.call("start")
            started_pid = int(pid)
            ready_pid = wait_running(self.port, (self.tag, data["build"]), self.request["pid"])
            self.runtime.call("inspect", "-ExpectedPid", ready_pid)
            if self.kind == "git" and not data["previous_tag"]:
                self.git("update-ref", f"refs/tags/{self.tag}", data["tag_object"], "0" * 40)
            write_status(self.folder, "success")
            self.transaction.unlink(missing_ok=True)
        except Exception as exc:
            log_error(self.folder, exc)
            error = (exc.code if isinstance(exc, R.UpdateError) else
                     "invalid_package" if isinstance(exc, BadZipFile) else "update_failed")
            rollback = None
            running = read_running(self.port) if stopping and not stopped else None
            if stopped or (stopping and (not running or running[2] != self.request["pid"])):
                write_status(self.folder, "rolling_back", error=error)
                try:
                    self.runtime.call("stop", "-StartedPid", started_pid)
                    self.restore(data)
                    self.runtime.call("start")
                    restored_pid = wait_running(self.port, data["previous_build"], self.request["pid"])
                    self.runtime.call("inspect", "-ExpectedPid", restored_pid)
                    rollback = "restored"
                except Exception as failed:
                    log_error(self.folder, failed)
                    rollback = "failed"
            if rollback != "failed":
                self.transaction.unlink(missing_ok=True)
            write_status(self.folder, "failed", error=error, rollback=rollback)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, required=True, metavar=("ROOT", "JOB"))
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    root = Path(args.worker[0]).resolve()
    job = args.worker[1]
    if not JOB_ID.fullmatch(job):
        return 2
    state = state_dir(root)
    folder = state / job
    if getattr(sys, "frozen", False):
        # Use the normal Windows DLL search path for PowerShell and the new panel.
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for _ in range(100):
        try:
            with lock(state / "worker.lock"):
                active = read_json(state / "active.json", {})
                if active.get("job") != job or read_json(folder / "status.json", {}).get("phase") != "queued":
                    return 2
                request = read_json(folder / "request.json")
                os.environ["GIT_TERMINAL_PROMPT"] = "0"
                os.environ["PYTHONUTF8"] = "1"
                worker = Worker(root, folder, request)
                try:
                    worker.execute()
                finally:
                    if args.scheduled:
                        try:
                            worker.runtime.call("remove")
                        except (R.UpdateError, OSError, subprocess.SubprocessError) as exc:
                            log_error(folder, exc)
                return 0
        except Busy:
            time.sleep(0.2)
    write_status(folder, "failed", error="busy")
    return 1


if __name__ == "__main__":
    sys.exit(main())
