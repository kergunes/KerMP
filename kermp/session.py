from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .buildsync import BuildAuthority
from .travel import TravelCoordinator


@dataclass(slots=True)
class Player:
    player_id: str
    display_name: str


@dataclass
class HostSession:
    session_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)
    session_name: str = "KerMP LAN"
    players: Dict[str, Player] = field(default_factory=dict)
    travel: TravelCoordinator = field(default_factory=TravelCoordinator)
    build: BuildAuthority = field(default_factory=BuildAuthority)

    def add_player(self, player_id: str, display_name: str) -> Player:
        p = Player(player_id, display_name)
        self.players[player_id] = p
        return p

    def remove_player(self, player_id: str) -> None:
        self.players.pop(player_id, None)
        if self.build.lock and self.build.lock.owner_id == player_id:
            self.build.lock = None
            self.build._lease_id = None
        self.travel.remove_participant(player_id)

    def snapshot(self) -> dict:
        return {"session_id": self.session_id, "session_name": self.session_name,
                "players": [{"player_id": p.player_id, "display_name": p.display_name}
                            for p in self.players.values()],
                "travel": self.travel.snapshot(), "build": self.build.snapshot()}
