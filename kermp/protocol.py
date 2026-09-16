from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 1
MAX_GAME_MESSAGE_BYTES = 2 * 1024 * 1024


class MessageType(str, Enum):
    HELLO = "hello"
    WELCOME = "welcome"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
    ACK = "ack"

    TRAVEL_REQUEST = "travel.request"
    TRAVEL_PROPOSE = "travel.propose"
    TRAVEL_READY = "travel.ready"
    TRAVEL_COMMIT = "travel.commit"
    TRAVEL_ZONE_READY = "travel.zone_ready"
    TRAVEL_RESUME = "travel.resume"
    TRAVEL_ABORT = "travel.abort"
    TRAVEL_VIEW_BATCH = "travel.view_batch"

    BUILD_LOCK_REQUEST = "build.lock_request"
    BUILD_LOCK_STATE = "build.lock_state"
    BUILD_OPERATION = "build.operation"
    BUILD_APPLY = "build.apply"
    BUILD_LOCK_RELEASE = "build.lock_release"

    SNAPSHOT_REQUEST = "snapshot.request"
    SNAPSHOT = "snapshot"
    SIM_SELECT = "sim.select"
    SIM_SELECTION_STATE = "sim.selection_state"
    SIM_STATE = "sim.state"
    INTERACTION_REQUEST = "interaction.request"
    INTERACTION_ACCEPTED = "interaction.accepted"
    INTERACTION_REJECTED = "interaction.rejected"
    INTERACTION_STARTED = "interaction.started"
    INTERACTION_FINISHED = "interaction.finished"
    INTERACTION_CANCEL = "interaction.cancel"
    GAME_RAW_MESSAGE = "game.raw_message"


@dataclass(slots=True)
class Envelope:
    type: str
    payload: Dict[str, Any]
    sender_id: str
    message_id: str
    sent_at: float
    seq: Optional[int] = None
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def make(
        cls,
        message_type: MessageType | str,
        payload: Optional[Dict[str, Any]],
        sender_id: str,
        *,
        seq: Optional[int] = None,
    ) -> "Envelope":
        return cls(
            type=str(message_type.value if isinstance(message_type, MessageType) else message_type),
            payload=payload or {},
            sender_id=sender_id,
            message_id=str(uuid.uuid4()),
            sent_at=time.time(),
            seq=seq,
        )

    def to_line(self) -> bytes:
        return (json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")

    @classmethod
    def from_line(cls, line: bytes | str) -> "Envelope":
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        raw = json.loads(line)
        if raw.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError(f"protocol mismatch: {raw.get('protocol_version')} != {PROTOCOL_VERSION}")
        required = {"type", "payload", "sender_id", "message_id", "sent_at", "protocol_version"}
        if not required.issubset(raw):
            raise ValueError("malformed envelope")
        return cls(**raw)
