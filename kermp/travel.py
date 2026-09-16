from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, Optional, Set
import uuid


class TravelPhase(str, Enum):
    IDLE = "idle"
    WAITING_READY = "waiting_ready"
    COMMITTED = "committed"
    WAITING_ZONE_READY = "waiting_zone_ready"
    COMPLETE = "complete"
    ABORTED = "aborted"


@dataclass
class TravelTxn:
    epoch: int
    txn_id: str
    zone_id: str
    actor_ids: list[str]
    participants: Set[str]
    phase: TravelPhase = TravelPhase.WAITING_READY
    ready: Set[str] = field(default_factory=set)
    zone_ready: Set[str] = field(default_factory=set)


class TravelCoordinator:
    """Host-side barrier coordinator.

    KerMP intentionally treats travel as an all-participants barrier: nobody resumes
    simulation until every participant reports the target zone loaded.
    """

    def __init__(self) -> None:
        self.current: Optional[TravelTxn] = None
        self._epoch = 0

    def propose(self, zone_id: str, actor_ids: Iterable[str], participants: Iterable[str]) -> TravelTxn:
        if self.current and self.current.phase not in {TravelPhase.COMPLETE, TravelPhase.ABORTED}:
            raise RuntimeError("travel already in progress")
        p = set(participants)
        if not p:
            raise ValueError("travel requires at least one participant")
        self._epoch += 1
        self.current = TravelTxn(
            epoch=self._epoch,
            txn_id=str(uuid.uuid4()),
            zone_id=str(zone_id),
            actor_ids=list(actor_ids),
            participants=p,
        )
        return self.current

    def mark_ready(self, player_id: str, txn_id: str) -> bool:
        t = self._get(txn_id)
        if t.phase != TravelPhase.WAITING_READY:
            return False
        if player_id in t.participants:
            t.ready.add(player_id)
        if t.ready == t.participants:
            t.phase = TravelPhase.COMMITTED
            return True
        return False

    def begin_zone_wait(self, txn_id: str) -> None:
        t = self._get(txn_id)
        if t.phase != TravelPhase.COMMITTED:
            raise RuntimeError("cannot wait for zone before commit")
        t.phase = TravelPhase.WAITING_ZONE_READY

    def mark_zone_ready(self, player_id: str, txn_id: str) -> bool:
        t = self._get(txn_id)
        if t.phase not in {TravelPhase.COMMITTED, TravelPhase.WAITING_ZONE_READY}:
            return False
        t.phase = TravelPhase.WAITING_ZONE_READY
        if player_id in t.participants:
            t.zone_ready.add(player_id)
        if t.zone_ready == t.participants:
            t.phase = TravelPhase.COMPLETE
            return True
        return False

    def abort(self, txn_id: str) -> None:
        self._get(txn_id).phase = TravelPhase.ABORTED

    def remove_participant(self, player_id: str) -> bool:
        if not self.current or self.current.phase in {TravelPhase.COMPLETE, TravelPhase.ABORTED}:
            return False
        self.current.participants.discard(player_id)
        self.current.ready.discard(player_id)
        self.current.zone_ready.discard(player_id)
        t = self.current
        if t.phase == TravelPhase.WAITING_READY and t.ready == t.participants:
            t.phase = TravelPhase.COMMITTED
            return True
        if t.phase == TravelPhase.WAITING_ZONE_READY and t.zone_ready == t.participants:
            t.phase = TravelPhase.COMPLETE
        return True

    def snapshot(self) -> Optional[dict]:
        t = self.current
        if not t or t.phase in {TravelPhase.COMPLETE, TravelPhase.ABORTED}:
            return None
        return {"txn_id": t.txn_id, "epoch": t.epoch, "zone_id": t.zone_id,
                "actor_ids": t.actor_ids, "participants": sorted(t.participants),
                "ready": sorted(t.ready), "zone_ready": sorted(t.zone_ready),
                "phase": t.phase.value}

    def _get(self, txn_id: str) -> TravelTxn:
        if not self.current or self.current.txn_id != txn_id:
            raise KeyError("unknown travel transaction")
        return self.current
