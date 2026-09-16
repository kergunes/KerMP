from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class PlayerLifecycle(str, Enum):
    CONNECTED = "CONNECTED"
    COMPATIBILITY_CHECKED = "COMPATIBILITY_CHECKED"
    COMPATIBLE = "COMPATIBLE"
    SAVE_SYNCING = "SAVE_SYNCING"
    SAVE_SYNCED = "SAVE_SYNCED"
    SIMS_BRIDGE_WAITING = "SIMS_BRIDGE_WAITING"
    SIMS_BRIDGE_CONNECTED = "SIMS_BRIDGE_CONNECTED"
    ZONE_LOADING = "ZONE_LOADING"
    ZONE_READY = "ZONE_READY"
    GAME_READY = "GAME_READY"
    IN_SESSION = "IN_SESSION"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


@dataclass(slots=True)
class PlayerReadiness:
    state: PlayerLifecycle = PlayerLifecycle.CONNECTED
    compatibility_reasons: tuple[str, ...] = ()
    save_bytes_received: int = 0
    save_total_bytes: int = 0
    save_hash: str = ""
    bridge_connected: bool = False
    zone_ready: bool = False
    simulation_authority_ready: bool = False
    native_ready: bool = False
    error: str = ""

    @property
    def save_progress(self) -> float:
        return self.save_bytes_received / self.save_total_bytes if self.save_total_bytes else 0.0

    @property
    def ready(self) -> bool:
        return (not self.compatibility_reasons and self.save_hash != "" and self.bridge_connected and
                self.zone_ready and self.simulation_authority_ready and self.native_ready and not self.error)

    def advance(self) -> None:
        if self.error:
            self.state = PlayerLifecycle.ERROR
        elif self.ready:
            self.state = PlayerLifecycle.GAME_READY
        elif self.zone_ready:
            self.state = PlayerLifecycle.ZONE_READY
        elif self.bridge_connected:
            self.state = PlayerLifecycle.SIMS_BRIDGE_CONNECTED
        elif self.save_hash:
            self.state = PlayerLifecycle.SAVE_SYNCED
        elif self.save_total_bytes:
            self.state = PlayerLifecycle.SAVE_SYNCING
        elif self.compatibility_reasons:
            self.state = PlayerLifecycle.COMPATIBILITY_CHECKED
        else:
            self.state = PlayerLifecycle.COMPATIBLE

    def snapshot(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        data["save_progress"] = self.save_progress
        data["ready"] = self.ready
        return data


@dataclass(frozen=True, slots=True)
class P0CoreReadiness:
    compatibility_ready: bool
    save_ready: bool
    bridge_ready: bool
    zone_ready: bool
    simulation_authority_ready: bool
    clock_ready: bool
    interaction_router_ready: bool
    dialog_router_ready: bool
    travel_ready: bool
    native_build_buy_ready: bool

    @property
    def core_ready(self) -> bool:
        return all(asdict(self).values())
