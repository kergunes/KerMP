from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AudienceKind(str, Enum):
    ALL_PLAYERS = "all_players"
    HOST_ONLY = "host_only"
    PLAYER = "player"
    ALL_EXCEPT = "all_except"


@dataclass(frozen=True, slots=True)
class PlayerAudience:
    kind: AudienceKind = AudienceKind.ALL_PLAYERS
    player_id: str | None = None
    def recipients(self, player_ids: set[str], host_id: str) -> set[str]:
        if self.kind is AudienceKind.HOST_ONLY: return {host_id}
        if self.kind is AudienceKind.PLAYER: return {self.player_id} & player_ids
        if self.kind is AudienceKind.ALL_EXCEPT: return player_ids - {self.player_id}
        return set(player_ids)


@dataclass(slots=True)
class RequestCorrelation:
    request_id: str
    player_id: str
    family: str
    created_at: float = field(default_factory=time.monotonic)
    completed: bool = False


class NetworkedCommandRouter:
    """Whitelist-only routing; handlers never receive a raw console command."""
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[str, dict], dict]] = {}
        self.pending: dict[str, RequestCorrelation] = {}
    def register(self, family: str, handler: Callable[[str, dict], dict]) -> None:
        self.handlers[family] = handler
    def dispatch(self, player_id: str, family: str, payload: dict, request_id: str | None = None) -> dict:
        request_id = request_id or uuid.uuid4().hex
        if request_id in self.pending: raise ValueError("duplicate_request_id")
        handler = self.handlers.get(family)
        if not handler: raise ValueError("command_not_allowed")
        self.pending[request_id] = RequestCorrelation(request_id, player_id, family)
        result = handler(player_id, dict(payload))
        self.pending[request_id].completed = True
        return {"request_id": request_id, "family": family, "result": result}


class DialogRouter:
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}
    def open(self, owner_player_id: str, dialog_type: str, payload: dict) -> dict:
        if len(str(payload)) > 64 * 1024: raise ValueError("dialog_payload_too_large")
        dialog_id = uuid.uuid4().hex
        record = {"dialog_id": dialog_id, "owner_player_id": owner_player_id, "dialog_type": dialog_type, "payload": dict(payload)}
        self.pending[dialog_id] = record
        return record
    def respond(self, player_id: str, dialog_id: str, response: dict) -> dict:
        record = self.pending.pop(dialog_id, None)
        if not record: raise ValueError("unknown_or_completed_dialog")
        if record["owner_player_id"] != player_id:
            self.pending[dialog_id] = record
            raise ValueError("dialog_not_owned")
        return {**record, "response": dict(response)}
    def disconnect(self, player_id: str) -> list[str]:
        stale = [key for key, value in self.pending.items() if value["owner_player_id"] == player_id]
        for key in stale: self.pending.pop(key, None)
        return stale
