from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

CHUNK_SIZE = 64 * 1024
MAX_CHUNK_SIZE = 256 * 1024
_SLOT = re.compile(r"^(Slot_[0-9A-Fa-f]+)\.save$")


def resolve_sims_user_dir(configured: str | Path | None = None) -> Path:
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("KERMP_SIM_USER_DIR"):
        return Path(os.environ["KERMP_SIM_USER_DIR"]).expanduser()
    documents = Path.home() / "Documents"
    one_drive = os.environ.get("OneDrive")
    candidates = [documents / "Electronic Arts" / "The Sims 4"]
    if one_drive:
        candidates.insert(0, Path(one_drive) / "Documents" / "Electronic Arts" / "The Sims 4")
    return next((path for path in candidates if path.exists()), candidates[0])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SaveSlot:
    slot_id: str
    path: Path
    size: int
    modified_at: float
    sha256: str

    @classmethod
    def from_path(cls, path: Path) -> "SaveSlot":
        match = _SLOT.match(path.name)
        if not match:
            raise ValueError("not_a_sims_save_slot")
        stat = path.stat()
        return cls(match.group(1), path, stat.st_size, stat.st_mtime, sha256_file(path))

    def manifest(self, session_id: str, chunk_size: int = CHUNK_SIZE) -> dict:
        if not 1 <= chunk_size <= MAX_CHUNK_SIZE:
            raise ValueError("invalid_chunk_size")
        count = (self.size + chunk_size - 1) // chunk_size
        return {"session_id": session_id, "slot_id": self.slot_id, "total_bytes": self.size,
                "chunk_size": chunk_size, "chunk_count": count, "sha256": self.sha256,
                "modified_at": self.modified_at}


def discover_slots(user_dir: str | Path | None = None) -> list[SaveSlot]:
    saves = resolve_sims_user_dir(user_dir) / "saves"
    result: list[SaveSlot] = []
    try:
        for path in saves.iterdir():
            if path.is_file() and _SLOT.match(path.name):
                result.append(SaveSlot.from_path(path))
    except OSError:
        pass
    return sorted(result, key=lambda slot: slot.modified_at, reverse=True)


class SaveReceiver:
    """Sequence-checked temporary writer; final slot is never written until hash validation."""
    def __init__(self, manifest: dict, destination_dir: Path) -> None:
        self.manifest = dict(manifest)
        self.destination_dir = destination_dir
        self.slot_id = str(manifest.get("slot_id") or "")
        self.total = int(manifest.get("total_bytes", -1))
        self.chunk_size = int(manifest.get("chunk_size", 0))
        self.chunk_count = int(manifest.get("chunk_count", -1))
        self.expected_hash = str(manifest.get("sha256") or "")
        if not _SLOT.match(self.slot_id + ".save") or self.total < 0 or not 1 <= self.chunk_size <= MAX_CHUNK_SIZE or self.chunk_count < 0 or len(self.expected_hash) != 64:
            raise ValueError("invalid_save_manifest")
        self.partial_path = destination_dir / (self.slot_id + ".kermp.partial")
        self.final_path = destination_dir / (self.slot_id + ".save")
        self.next_index = 0
        self.received = 0
        destination_dir.mkdir(parents=True, exist_ok=True)
        self.partial_path.unlink(missing_ok=True)

    def write_chunk(self, index: int, data: bytes) -> None:
        if index != self.next_index:
            raise ValueError("save_chunk_out_of_order")
        if len(data) > self.chunk_size or self.received + len(data) > self.total:
            raise ValueError("save_chunk_oversized")
        expected_final = index == self.chunk_count - 1
        if not expected_final and len(data) != self.chunk_size:
            raise ValueError("save_chunk_short")
        with self.partial_path.open("ab") as stream:
            stream.write(data)
        self.next_index += 1
        self.received += len(data)

    def abort(self) -> None:
        self.partial_path.unlink(missing_ok=True)

    def finalize(self, session_id: str) -> tuple[Path, Path | None]:
        if self.next_index != self.chunk_count or self.received != self.total or sha256_file(self.partial_path) != self.expected_hash:
            self.abort()
            raise ValueError("save_hash_mismatch")
        backup = None
        if self.final_path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = self.destination_dir / "KerMP backups" / session_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / (self.slot_id + "." + stamp + ".save")
            shutil.copy2(self.final_path, backup)
        os.replace(self.partial_path, self.final_path)
        return self.final_path, backup
