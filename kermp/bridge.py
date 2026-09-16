from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional


@dataclass(slots=True)
class BridgeEvent:
    type: str
    payload: Dict[str, Any]


class LocalGameBridge:
    """Localhost line-delimited JSON bridge between sidecar and Sims script mod.

    The sidecar listens; the in-game script connects to 127.0.0.1 only. Keeping the
    game-facing protocol local means LAN transport can evolve independently.
    """

    def __init__(self, port: int = 17654, on_event: Optional[Callable[[BridgeEvent], None]] = None) -> None:
        self.port = port
        self.on_event = on_event or (lambda event: None)
        self._server: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="kermp-local-bridge", daemon=True)
        self._thread.start()

    def send(self, event_type: str, payload: Dict[str, Any]) -> bool:
        conn = self._conn
        if not conn:
            return False
        wire = json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")) + "\n"
        try:
            conn.sendall(wire.encode("utf-8"))
            return True
        except OSError:
            self._conn = None
            return False

    def close(self) -> None:
        self._stop.set()
        for s in (self._conn, self._server):
            try:
                if s:
                    s.close()
            except OSError:
                pass

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            self._server = srv
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.port))
            srv.listen(1)
            srv.settimeout(0.5)
            while not self._stop.is_set():
                if not self._conn:
                    try:
                        conn, _ = srv.accept()
                        conn.settimeout(0.5)
                        self._conn = conn
                    except socket.timeout:
                        continue
                try:
                    assert self._conn is not None
                    buf = self._conn.makefile("rb")
                    while not self._stop.is_set():
                        line = buf.readline()
                        if not line:
                            break
                        raw = json.loads(line.decode("utf-8"))
                        self.on_event(BridgeEvent(raw["type"], raw.get("payload", {})))
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
                finally:
                    try:
                        if self._conn:
                            self._conn.close()
                    except OSError:
                        pass
                    self._conn = None
