from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from . import KERMP_VERSION, NATIVE_API_VERSION, PROTOCOL_VERSION, SIMS_MOD_PROTOCOL_VERSION

UNKNOWN = "UNKNOWN"


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(sorted({part.strip().upper() for part in (value or "").split(",") if part.strip()}))


def detect_game_version(sims_user_dir: Path | None = None) -> str:
    """Read a user-supplied/current user folder without guessing a game build."""
    roots = [sims_user_dir] if sims_user_dir else []
    env = os.environ.get("KERMP_SIM_USER_DIR")
    if env:
        roots.append(Path(env))
    roots.append(Path.home() / "Documents" / "Electronic Arts" / "The Sims 4")
    for root in roots:
        if not root:
            continue
        for name in ("GameVersion.txt", "gameversion.txt"):
            try:
                value = (root / name).read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                pass
    return UNKNOWN


def package_hash(path: Path | None) -> str:
    if not path:
        return UNKNOWN
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return UNKNOWN


@dataclass(frozen=True, slots=True)
class CompatibilityManifest:
    player_id: str
    display_name: str
    kermp_protocol_version: int = PROTOCOL_VERSION
    kermp_sidecar_version: str = KERMP_VERSION
    kermp_sims_mod_version: str = KERMP_VERSION
    sims_mod_protocol_version: int = SIMS_MOD_PROTOCOL_VERSION
    native_available: bool = False
    native_api_version: int = NATIVE_API_VERSION
    native_game_build_supported: bool = False
    game_version: str = UNKNOWN
    enabled_packs: tuple[str, ...] = field(default_factory=tuple)
    installed_packs: tuple[str, ...] = field(default_factory=tuple)
    sims_mod_package_hash: str = UNKNOWN
    native_build_fingerprint: str = UNKNOWN

    @classmethod
    def local(cls, player_id: str, display_name: str, *, sims_user_dir: Path | None = None,
              native_available: bool = False, native_game_build_supported: bool = False,
              package_path: Path | None = None) -> "CompatibilityManifest":
        return cls(player_id=player_id, display_name=display_name,
                   native_available=native_available, native_game_build_supported=native_game_build_supported,
                   game_version=detect_game_version(sims_user_dir),
                   enabled_packs=_csv(os.environ.get("KERMP_ENABLED_PACKS")),
                   installed_packs=_csv(os.environ.get("KERMP_INSTALLED_PACKS")),
                   sims_mod_package_hash=package_hash(package_path))

    @classmethod
    def from_dict(cls, value: dict) -> "CompatibilityManifest":
        allowed = {key: value[key] for key in cls.__dataclass_fields__ if key in value}
        for key in ("enabled_packs", "installed_packs"):
            if key in allowed:
                allowed[key] = tuple(sorted(map(str, allowed[key] or ())))
        return cls(**allowed)

    def to_dict(self) -> dict:
        return asdict(self)


def compare_manifests(host: CompatibilityManifest, client: CompatibilityManifest, *, native_required: bool) -> list[str]:
    reasons: list[str] = []
    if host.kermp_protocol_version != client.kermp_protocol_version or host.sims_mod_protocol_version != client.sims_mod_protocol_version:
        reasons.append("protocol_mismatch")
    if host.kermp_sidecar_version != client.kermp_sidecar_version:
        reasons.append("sidecar_version_mismatch")
    if host.kermp_sims_mod_version != client.kermp_sims_mod_version:
        reasons.append("sims_mod_mismatch")
    if UNKNOWN in (host.game_version, client.game_version) or host.game_version != client.game_version:
        reasons.append("game_version_mismatch")
    if tuple(host.enabled_packs) != tuple(client.enabled_packs):
        reasons.append("pack_mismatch")
    if native_required and (not host.native_available or not client.native_available or
                            not host.native_game_build_supported or not client.native_game_build_supported or
                            host.native_api_version != client.native_api_version):
        reasons.append("native_api_mismatch")
    return reasons
