from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .buildsync import BuildAuthority
from .travel import TravelCoordinator


@dataclass(slots=True)
class Player:
    player_id: str
    display_name: str
    active_sim_id: Optional[str] = None


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

    def add_player(self, player_id: str, display_name: str) -> Player:
        p = Player(player_id, display_name)
        self.players[player_id] = p
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

    def snapshot(self) -> dict:
        return {"session_id": self.session_id, "session_name": self.session_name,
                "players": [{"player_id": p.player_id, "display_name": p.display_name,
                             "active_sim_id": p.active_sim_id}
                            for p in self.players.values()],
                "sims": list(self.sims.values()), "travel": self.travel.snapshot(), "build": self.build.snapshot()}

    def update_sims(self, sims: list[dict]) -> None:
        previous = self.sims
        self.sims = {str(s["sim_id"]): {"sim_id": str(s["sim_id"]), "name": str(s.get("name", "")),
                                        "controllers": list(s.get("controllers") or previous.get(str(s["sim_id"]), {}).get("controllers", []))}
                     for s in sims if s.get("sim_id") is not None}
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
                  "position": payload.get("position"), "status": "accepted"}
        self.interaction_requests[request_id] = result
        return result
