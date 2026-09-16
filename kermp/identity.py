from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(slots=True)
class Identity:
    player_id: str
    display_name: str


class IdentityStore:
    def __init__(self, path: Path | None = None) -> None:
        default = Path(os.getenv("APPDATA", Path.home())) / "KerMP" / "identity.json"
        self.path = path or default

    def load_or_create(self, display_name: str | None = None) -> Identity:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ident = Identity(**data)
            if display_name and display_name != ident.display_name:
                ident.display_name = display_name
                self.save(ident)
            return ident

        ident = Identity(player_id=str(uuid.uuid4()), display_name=display_name or "Player")
        self.save(ident)
        return ident

    def save(self, identity: Identity) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(identity), indent=2), encoding="utf-8")
