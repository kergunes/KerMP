from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Optional

from .buildsync import BuildAuthority
from .compatibility import CompatibilityManifest, compare_manifests
from .readiness import P0CoreReadiness, PlayerReadiness
from .routing import DialogRouter, NetworkedCommandRouter
from .travel import TravelCoordinator


@dataclass(slots=True)
class Player:
    player_id: str
    display_name: str
    active_sim_id: Optional[str] = None
    manifest: Optional[CompatibilityManifest] = None
    readiness: PlayerReadiness = field(default_factory=PlayerReadiness)


@dataclass
class HostSession:
    session_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)
    session_name: str = "KerMP LAN"
    players: Dict[str, Player] = field(default_factory=dict)
    travel: TravelCoordinator = field(default_factory=TravelCoordinator)
    build: BuildAuthority = field(default_factory=BuildAuthority)
    sims: Dict[str, dict] = field(default_factory=dict)
    interaction_requests: Dict[str, dict] = field(default_factory=dict)
    reconnect_sim_ids: Dict[str, str] = field(default_factory=dict)
    host_manifest: Optional[CompatibilityManifest] = None
    native_build_buy_required: bool = False
    clock: dict = field(default_factory=lambda: {"sequence": 0, "game_time": None, "speed": 1, "paused": False})
    commands: NetworkedCommandRouter = field(default_factory=NetworkedCommandRouter)
    dialogs: DialogRouter = field(default_factory=DialogRouter)

    def add_player(self, player_id: str, display_name: str, manifest: Optional[CompatibilityManifest] = None) -> Player:
        p = Player(player_id, display_name)
        self.players[player_id] = p
        if manifest:
            self.set_manifest(player_id, manifest)
        saved = self.reconnect_sim_ids.get(player_id)
        if saved in self.sims:
            p.active_sim_id = saved
            self._set_sim_controllers(saved)
        return p

    def remove_player(self, player_id: str) -> None:
        player = self.players.get(player_id)
        if player and player.active_sim_id:
            self.reconnect_sim_ids[player_id] = player.active_sim_id
        self.players.pop(player_id, None)
        for sim_id in self.sims:
            self._set_sim_controllers(sim_id)
        if self.build.lock and self.build.lock.owner_id == player_id:
            self.build.lock = None
            self.build._lease_id = None
        self.travel.remove_participant(player_id)
        self.dialogs.disconnect(player_id)

    def snapshot(self) -> dict:
        return {"session_id": self.session_id, "session_name": self.session_name,
                "players": [{"player_id": p.player_id, "display_name": p.display_name,
                             "active_sim_id": p.active_sim_id}
                            for p in self.players.values()],
                "sims": list(self.sims.values()), "travel": self.travel.snapshot(), "build": self.build.snapshot(),
                "clock": dict(self.clock), "readiness": {pid: p.readiness.snapshot() for pid, p in self.players.items()},
                "core_readiness": asdict(self.core_readiness()) | {"core_ready": self.core_readiness().core_ready}}

    def set_manifest(self, player_id: str, manifest: CompatibilityManifest) -> list[str]:
        player = self.players[player_id]
        player.manifest = manifest
        reasons = compare_manifests(self.host_manifest, manifest, native_required=self.native_build_buy_required) if self.host_manifest and player_id != self.host_manifest.player_id else []
        player.readiness.compatibility_reasons = tuple(reasons)
        player.readiness.native_ready = bool(manifest.native_available and manifest.native_game_build_supported) if self.native_build_buy_required else True
        player.readiness.advance()
        return reasons

    def update_readiness(self, player_id: str, **fields: object) -> PlayerReadiness:
        ready = self.players[player_id].readiness
        for key, value in fields.items():
            if not hasattr(ready, key): raise ValueError("unknown_readiness_field")
            setattr(ready, key, value)
        ready.advance()
        return ready

    def core_readiness(self) -> P0CoreReadiness:
        players = list(self.players.values())
        return P0CoreReadiness(
            compatibility_ready=bool(players) and all(not p.readiness.compatibility_reasons for p in players),
            save_ready=bool(players) and all(bool(p.readiness.save_hash) for p in players),
            bridge_ready=bool(players) and all(p.readiness.bridge_connected for p in players),
            zone_ready=bool(players) and all(p.readiness.zone_ready for p in players),
            simulation_authority_ready=bool(players) and all(p.readiness.simulation_authority_ready for p in players),
            clock_ready=bool(self.clock), interaction_router_ready=True, dialog_router_ready=True,
            travel_ready=True,
            native_build_buy_ready=(not self.native_build_buy_required or all(p.readiness.native_ready for p in players)),
        )

    def update_sims(self, sims: list[dict]) -> None:
        previous = self.sims
        # Controllers are derived only from authoritative Player state.
        self.sims = {str(s["sim_id"]): {"sim_id": str(s["sim_id"]), "name": str(s.get("name", "")),
                                        "controllers": list(previous.get(str(s["sim_id"]), {}).get("controllers", []))}
                     for s in sims if s.get("sim_id") is not None and str(s.get("sim_id"))}
        for sim_id in self.sims:
            self._set_sim_controllers(sim_id)

    def sim_owner(self, sim_id: str) -> Optional[str]:
        return next((p.player_id for p in self.players.values() if p.active_sim_id == str(sim_id)), None)

    def select_sim(self, player_id: str, sim_id: str) -> dict:
        sim_id = str(sim_id)
        if player_id not in self.players: raise ValueError("unknown_player")
        if sim_id not in self.sims: raise ValueError("invalid_sim_id")
        player = self.players[player_id]
        player.active_sim_id = sim_id
        self._set_sim_controllers(sim_id)
        return {"player_id": player_id, "sim_id": sim_id, "name": self.sims[sim_id]["name"],
                "controllers": list(self.sims[sim_id]["controllers"])}

    def _set_sim_controllers(self, sim_id: str) -> None:
        controllers = [p.player_id for p in self.players.values() if p.active_sim_id == str(sim_id)]
        self.sims[sim_id]["controllers"] = controllers

    def validate_interaction(self, request_id: str, player_id: str, payload: dict) -> dict:
        request_id = str(request_id or "")
        player = self.players.get(player_id)
        sim_id = str(payload.get("sim_id") or (player.active_sim_id if player else ""))
        if not request_id: raise ValueError("missing_request_id")
        if request_id in self.interaction_requests: raise ValueError("duplicate_request_id")
        if not player or sim_id != player.active_sim_id: raise ValueError("sim_not_owned")
        if sim_id not in self.sims: raise ValueError("invalid_sim_id")
        affordance_id = str(payload.get("affordance_id") or "")
        if not affordance_id: raise ValueError("missing_affordance_id")
        result = {"request_id": request_id, "player_id": player_id, "sim_id": sim_id,
                  "affordance_id": affordance_id, "target_id": payload.get("target_id"),
                  "position": payload.get("position"),
                  "interaction_kwargs": dict(payload.get("interaction_kwargs") or {}),
                  "status": "accepted"}
        self.interaction_requests[request_id] = result
        return result

    def cancel_interaction(self, request_id: str, player_id: str, payload: dict) -> dict:
        request_id = str(request_id or "")
        player = self.players.get(player_id)
        sim_id = str(payload.get("sim_id") or "")
        if not player or sim_id != player.active_sim_id:
            raise ValueError("sim_not_owned")
        interaction_id = payload.get("interaction_id")
        if interaction_id in (None, "", 0, "0"):
            raise ValueError("missing_interaction_id")
        if not request_id:
            request_id = "cancel-%s-%s" % (player_id, interaction_id)
        request = self.interaction_requests.get(request_id)
        if request is None:
            request = {"request_id": request_id, "player_id": player_id, "sim_id": sim_id,
                       "affordance_id": None, "target_id": None, "position": None}
            self.interaction_requests[request_id] = request
        request["status"] = "cancel_requested"
        return {"request_id": request_id, "player_id": player_id, "sim_id": sim_id,
                "interaction_id": interaction_id,
                "context_handle": payload.get("context_handle"), "status": request["status"]}
