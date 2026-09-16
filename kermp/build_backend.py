"""Clean boundary between transport and the Sims Build/Buy implementation."""
from __future__ import annotations
from typing import Any

class BuildBackend:
    def capture_local_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
    def apply_remote_operation(self, operation: dict[str, Any]) -> bool:
        raise NotImplementedError
    def set_build_authority(self, owner_id: str | None) -> None:
        raise NotImplementedError
    def is_available(self) -> bool:
        return False
    def capabilities(self) -> set[str]:
        return set()

class FakeBuildBackend(BuildBackend):
    def __init__(self) -> None:
        self.applied: list[dict[str, Any]] = []
        self.authority: str | None = None
    def capture_local_operation(self, operation): return dict(operation)
    def apply_remote_operation(self, operation):
        if operation not in self.applied: self.applied.append(dict(operation))
        return True
    def set_build_authority(self, owner_id): self.authority = owner_id
    def is_available(self): return True
    def capabilities(self): return {"wall.create", "wall.delete"}

class SimsNativeBuildBackend(BuildBackend):
    """Explicit unavailable boundary until a tested Sims native hook exists."""
    def capabilities(self): return set()
