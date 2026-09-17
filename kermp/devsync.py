"""Reliable KerMP development-source synchronizer.

Run this before starting The Sims 4 and leave it open during development:

    python -m kermp.devsync

Every accepted source generation is first compiled by the existing Python 3.7
build pipeline, then copied atomically into Mods/KerMP/Scripts.  A generation
manifest is written last, so the in-game reloader never consumes a half-written
batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sims_mod_src" / "kermp_mod"
BUILD_SCRIPT = ROOT / "sims_mod_src" / "build.py"

MANIFEST_NAME = ".kermp-dev-manifest.json"
REQUEST_NAME = ".kermp-reload-request.json"
ACK_NAME = ".kermp-reload-ack.json"
SYNCING_NAME = ".kermp-syncing"
LOCK_NAME = ".kermp-dev.lock"

STABLE_MODULES = {
    "kermp_mod.__init__",
    "kermp_mod.bridge_client",
    "kermp_mod.runtime_state",
    "kermp_mod.reload_core",
    "kermp_mod.dev_reload",
    "kermp_mod.commands",
    "kermp_mod.build_adapter",
}


@dataclass(frozen=True)
class SyncResult:
    generation: int
    changed_modules: tuple[str, ...]
    deleted_modules: tuple[str, ...]
    restart_required: tuple[str, ...]
    initial_install: bool


def default_install_dir() -> Path:
    user_dir = os.environ.get("KERMP_SIM_USER_DIR")
    if user_dir:
        return Path(user_dir) / "Mods" / "KerMP"
    return Path.home() / "Documents" / "Electronic Arts" / "The Sims 4" / "Mods" / "KerMP"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sources(source_root: Path = SOURCE_ROOT) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(source_root.rglob("*.py")):
        result[path.relative_to(source_root).as_posix()] = _sha256(path)
    return result


def module_name(relative_path: str) -> str:
    value = relative_path.replace("\\", "/")
    if value == "__init__.py":
        return "kermp_mod.__init__"
    if not value.endswith(".py"):
        raise ValueError("not a Python source: %s" % relative_path)
    return "kermp_mod." + value[:-3].replace("/", ".")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-%s" % os.getpid())
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _atomic_write_json(path: Path, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(path, raw)


def _atomic_copy(source: Path, destination: Path) -> None:
    _atomic_write_bytes(destination, source.read_bytes())
    try:
        shutil.copystat(source, destination)
    except OSError:
        pass


def _default_build_archive() -> None:
    env = os.environ.copy()
    env["KERMP_KEEP_DEV_SCRIPTS"] = "1"
    subprocess.run([sys.executable, str(BUILD_SCRIPT)], cwd=str(ROOT), env=env, check=True)


def sync_once(
    install_dir: Path,
    *,
    source_root: Path = SOURCE_ROOT,
    build_archive: Callable[[], None] | None = None,
    force: bool = False,
) -> SyncResult | None:
    """Validate/build and publish exactly one source generation."""
    build_archive = build_archive or _default_build_archive
    scripts_root = install_dir / "Scripts"
    target_root = scripts_root / "kermp_mod"
    manifest_path = scripts_root / MANIFEST_NAME
    request_path = scripts_root / REQUEST_NAME
    syncing_path = scripts_root / SYNCING_NAME

    current = snapshot_sources(source_root)
    old_manifest = _read_json(manifest_path)
    previous = old_manifest.get("files") if isinstance(old_manifest.get("files"), dict) else {}
    initial = not bool(old_manifest)

    changed = sorted(path for path, digest in current.items() if previous.get(path) != digest)
    deleted = sorted(path for path in previous if path not in current)
    if not force and not initial and not changed and not deleted:
        return None

    # The build is the authoritative Python 3.7 syntax/bytecode validation.
    # It runs before the loose-source generation becomes visible to the game.
    build_archive()

    generation = int(old_manifest.get("generation", 0) or 0) + 1
    scripts_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(syncing_path, {"generation": generation, "pid": os.getpid()})
    try:
        for relative in changed:
            _atomic_copy(source_root / relative, target_root / relative)
        for relative in deleted:
            destination = target_root / relative
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            parent = destination.parent
            while parent != target_root and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        changed_modules = tuple(module_name(path) for path in changed)
        deleted_modules = tuple(module_name(path) for path in deleted)
        restart_required = sorted(
            set(name for name in changed_modules if name in STABLE_MODULES)
            | set(deleted_modules)
        )

        manifest = {
            "version": 1,
            "generation": generation,
            "files": current,
            "changed_modules": list(changed_modules),
            "deleted_modules": list(deleted_modules),
            "restart_required": restart_required,
            "initial_install": initial,
        }
        _atomic_write_json(manifest_path, manifest)

        request = {
            "version": 1,
            "generation": generation,
            "modules": [] if initial else list(changed_modules),
            "deleted_modules": [] if initial else list(deleted_modules),
            "restart_required": [] if initial else restart_required,
            "initial_install": initial,
        }
        _atomic_write_json(request_path, request)
        return SyncResult(
            generation=generation,
            changed_modules=changed_modules,
            deleted_modules=deleted_modules,
            restart_required=tuple(restart_required),
            initial_install=initial,
        )
    finally:
        try:
            syncing_path.unlink()
        except FileNotFoundError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            value = _read_json(self.path)
            pid = int(value.get("pid", 0) or 0)
            if _pid_alive(pid):
                raise RuntimeError("KerMP dev watcher already running with pid=%s" % pid)
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(self.path), flags, 0o600)
        try:
            os.write(fd, json.dumps({"pid": os.getpid()}).encode("utf-8"))
        finally:
            os.close(fd)
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False


def watch(
    install_dir: Path,
    *,
    poll_seconds: float = 0.20,
    debounce_seconds: float = 0.35,
) -> None:
    scripts_root = install_dir / "Scripts"
    lock_path = install_dir / LOCK_NAME
    with ProcessLock(lock_path):
        result = sync_once(install_dir, force=True)
        assert result is not None
        print("KerMP dev mode ready. generation=%s" % result.generation)
        print("Open/restart The Sims 4 once after the first dev install.")
        print("Then edit source and run 'kermp.reload' in the game console after each accepted save.")
        last_seen = snapshot_sources()
        dirty_since: float | None = None
        while True:
            time.sleep(poll_seconds)
            observed = snapshot_sources()
            if observed != last_seen:
                last_seen = observed
                dirty_since = time.monotonic()
                continue
            if dirty_since is None or time.monotonic() - dirty_since < debounce_seconds:
                continue
            try:
                result = sync_once(install_dir)
                if result is not None:
                    print(
                        "Synced generation=%s changed=%s deleted=%s restart_required=%s"
                        % (
                            result.generation,
                            ",".join(result.changed_modules) or "none",
                            ",".join(result.deleted_modules) or "none",
                            ",".join(result.restart_required) or "none",
                        )
                    )
            except subprocess.CalledProcessError as exc:
                print("KerMP sync rejected: Python 3.7 build failed (exit=%s)." % exc.returncode)
            except Exception as exc:
                print("KerMP sync rejected: %s: %s" % (type(exc).__name__, exc))
            finally:
                # Re-read the actual tree: editors may have saved again while
                # compilation was running.
                last_seen = snapshot_sources()
                dirty_since = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KerMP Sims 4 live-development synchronizer")
    parser.add_argument("--once", action="store_true", help="build and sync one generation, then exit")
    parser.add_argument("--poll-ms", type=int, default=200)
    parser.add_argument("--debounce-ms", type=int, default=350)
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    args = parser.parse_args(argv)

    install_dir = args.install_dir.expanduser().resolve()
    if args.once:
        with ProcessLock(install_dir / LOCK_NAME):
            result = sync_once(install_dir, force=True)
        if result:
            print("KerMP dev sync complete generation=%s" % result.generation)
        return 0

    try:
        watch(
            install_dir,
            poll_seconds=max(0.05, args.poll_ms / 1000.0),
            debounce_seconds=max(0.05, args.debounce_ms / 1000.0),
        )
    except KeyboardInterrupt:
        print("\nKerMP dev watcher stopped; installed dev files were left intact.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
