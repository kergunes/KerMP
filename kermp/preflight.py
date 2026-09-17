"""Multiplayer preflight: inspect the local environment for a KerMP session.

Produces a human-readable checklist so a two-PC morning setup fails with an
actionable message instead of a mysterious connection error. Never modifies
firewall rules or game state.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path

LAN_PORT = 17653
BRIDGE_PORT = 17654


def sims_user_dir() -> Path:
    if os.environ.get("KERMP_SIM_USER_DIR"):
        return Path(os.environ["KERMP_SIM_USER_DIR"])
    return Path.home() / "Documents" / "Electronic Arts" / "The Sims 4"


def mods_dir() -> Path:
    return sims_user_dir() / "Mods"


def saves_dir() -> Path:
    return sims_user_dir() / "saves"


def packaged_mod() -> Path:
    return mods_dir() / "KerMP" / "KerMP.ts4script"


def dev_mod_tree() -> Path:
    return mods_dir() / "KerMP" / "Scripts" / "kermp_mod"


def _game_version() -> str:
    for name in ("GameVersion.txt", "gameversion.txt"):
        p = sims_user_dir() / name
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace").strip()
                text = text.replace("\x00", "").replace("\ufeff", "").strip()
                return text.splitlines()[0][:80] if text else "unknown"
            except OSError:
                pass
    return "unknown"


def _port_available(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def detected_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _py37_available() -> bool:
    if os.environ.get("PYTHON37"):
        return Path(os.environ["PYTHON37"]).exists()
    for name in ("py -3.7", "python3.7", "python37"):
        if shutil.which(name.split()[0]):
            return True
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "Programs" / "Python" / "Python37" / "python.exe"
        if p.exists():
            return True
    return False


def _check(label: str, ok: bool, detail: str = "") -> str:
    status = "OK  " if ok else "FAIL"
    line = "[%s] %s" % (status, label)
    return line + ("  -> " + detail if detail else "")


def _info(label: str, detail: str = "") -> str:
    return "[INFO] %s" % label + ("  -> " + detail if detail else "")


def run_preflight() -> list[str]:
    lines: list[str] = []
    lines.append("KerMP multiplayer preflight")
    lines.append("=" * 40)

    lines.append(_check("Python environment", sys.version_info >= (3, 10),
                        "python %s" % sys.version.split()[0]))
    try:
        import kermp  # noqa: F401
        version = getattr(kermp, "__version__", "unknown")
    except Exception:
        version = "not-installed"
    lines.append(_check("KerMP package", version != "not-installed", "version %s" % version))

    udir = sims_user_dir()
    lines.append(_check("Sims user folder", udir.exists(), str(udir)))
    lines.append(_check("Sims Mods folder", mods_dir().exists(), str(mods_dir())))
    lines.append(_check("Sims saves folder", saves_dir().exists(), str(saves_dir())))
    lines.append(_check("Game version", _game_version() != "unknown", _game_version()))

    packaged = packaged_mod().exists()
    dev = dev_mod_tree().exists()
    lines.append(_check("KerMP packaged mod", packaged, str(packaged_mod())))
    lines.append(_info("KerMP dev source tree", ("installed " + str(dev_mod_tree())) if dev else "not installed (packaged mode)"))
    if packaged and dev:
        lines.append(_check("No packaged/dev double-load", False,
                            "remove one: run build.py --dev (dev) or build.py (packaged)"))

    py37 = _py37_available()
    lines.append(_check("Python 3.7 (Sims mod build)", py37,
                        "set PYTHON37 to a python 3.7 exe, or install it, to rebuild the .ts4script"))

    lines.append(_check("LAN port %s" % LAN_PORT, _port_available(LAN_PORT)))
    lines.append(_check("Bridge port %s" % BRIDGE_PORT, _port_available(BRIDGE_PORT)))
    lines.append("LAN address: %s" % detected_lan_ip())
    lines.append("Save path:   %s" % str(saves_dir()))
    lines.append("")
    lines.append("If any line is FAIL, fix it before Host/Join. Windows Firewall must allow")
    lines.append("python/KerMP on Private networks (this tool does not modify firewall rules).")
    return lines


def main() -> None:
    for line in run_preflight():
        print(line)


if __name__ == "__main__":
    main()
