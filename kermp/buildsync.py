from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Iterable
import time


@dataclass(slots=True)
class BuildLock:
    owner_id: str
    acquired_at: float
    lease_seconds: float

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.acquired_at > self.lease_seconds


@dataclass(slots=True)
class BuildOperation:
    operation_id: str
    seq: int
    player_id: str
    op: str
    data: Dict[str, Any]


class BuildAuthority:
    """Host-side serialized build operation stream.

    v0.0.1 deliberately uses one global build lease. This avoids same-wall conflict
    handling while we prove wall create/delete replication first.
    """

    def __init__(self, lease_seconds: float = 8.0, history_limit: int = 128) -> None:
        self.lease_seconds = lease_seconds
        self.lock: Optional[BuildLock] = None
        self._seq = 0
        self._history_limit = history_limit
        self._history: list[BuildOperation] = []
        self._lease_id: Optional[str] = None

    def request_lock(self, player_id: str) -> bool:
        if self.lock and self.lock.expired:
            self.lock = None
        if self.lock is None or self.lock.owner_id == player_id:
            self.lock = BuildLock(player_id, time.monotonic(), self.lease_seconds)
            self._lease_id = f"{player_id}:{int(time.time_ns())}"
            return True
        return False

    def release_lock(self, player_id: str) -> bool:
        if self.lock and self.lock.owner_id == player_id:
            self.lock = None
            self._lease_id = None
            return True
        return False

    def submit(self, player_id: str, op: str, data: Dict[str, Any]) -> BuildOperation:
        if self.lock and self.lock.expired:
            self.lock = None
        if not self.lock or self.lock.owner_id != player_id:
            raise PermissionError("build operation submitted without authority")
        self.lock.acquired_at = time.monotonic()
        self._seq += 1
        operation = BuildOperation(f"build-{self._seq}", self._seq, player_id, op, data)
        self._history.append(operation)
        del self._history[:-self._history_limit]
        return operation

    @property
    def lease_id(self) -> Optional[str]:
        return self._lease_id

    def history_since(self, seq: int) -> list[BuildOperation]:
        return [op for op in self._history if op.seq > seq]

    def snapshot(self) -> dict:
        return {
            "owner_id": self.lock.owner_id if self.lock and not self.lock.expired else None,
            "lease_id": self._lease_id,
            "current_sequence": self._seq,
            "recent_operations": [op.__dict__ if hasattr(op, "__dict__") else {
                "operation_id": op.operation_id, "seq": op.seq, "player_id": op.player_id,
                "op": op.op, "data": op.data} for op in self._history[-20:]],
        }
