"""Diagnostic remote Player 2 for single-PC real-Sims testing."""
from __future__ import annotations

import argparse, base64, json, socket, sys, tempfile, threading, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kermp.compatibility import CompatibilityManifest
from kermp.protocol import Envelope, MessageType
from kermp.save_sync import SaveReceiver


def parse_command(line: str) -> tuple[str, list[str]]:
    parts = line.strip().split()
    return (parts[0].lower(), parts[1:]) if parts else ("", [])


def move_payload(object_id: str, values: list[str]) -> dict:
    if len(values) != 7:
        raise ValueError("usage: move <object_id> <x> <y> <z> <qx> <qy> <qz> <qw>")
    return {"object_id": str(object_id), "transform": {
        "translation": [float(v) for v in values[:3]],
        "orientation": [float(v) for v in values[3:]],
    }}


@dataclass
class FakePlayerState:
    player_id: str
    connection: str = "disconnected"
    session_id: str = ""
    compatibility: Any = "unknown"
    readiness: dict = field(default_factory=dict)
    zone: Any = None
    clock: dict = field(default_factory=dict)
    build_lock_owner: Any = None
    selected_sim: Any = None
    dialogs: dict = field(default_factory=dict)
    travel: dict = field(default_factory=lambda: {"phase": "idle", "txn_id": None, "epoch": None, "zone_id": None, "batch_open": False, "last_error": ""})
    raw_messages_received: int = 0
    last_msg_id: Any = None
    last_size: Any = None
    last_command: str = ""
    last_request_id: Any = None
    last_result: Any = None
    last_error: str = ""

    def apply_snapshot(self, snapshot: dict) -> None:
        self.session_id = str(snapshot.get("session_id") or self.session_id)
        self.clock = dict(snapshot.get("clock") or self.clock)
        self.build_lock_owner = (snapshot.get("build") or {}).get("owner_id")
        own = (snapshot.get("readiness") or {}).get(self.player_id)
        if own: self.readiness = dict(own)
        for player in snapshot.get("players") or []:
            if player.get("player_id") == self.player_id: self.selected_sim = player.get("active_sim_id")
        if snapshot.get("travel"): self.update_travel("travel.propose", snapshot["travel"])

    def update_travel(self, typ: str, payload: dict) -> None:
        phase = {"travel.propose": "propose", "travel.commit": "commit", "travel.resume": "resume", "travel.abort": "abort"}.get(typ)
        if typ == MessageType.TRAVEL_VIEW_BATCH.value:
            self.travel["batch_open"] = str(payload.get("kind", "")).lower() == "begin"
            phase = "view_batch_" + str(payload.get("kind", "")).lower()
        if phase: self.travel["phase"] = phase
        for key in ("txn_id", "epoch", "zone_id"):
            if key in payload: self.travel[key] = payload[key]
        if typ == MessageType.TRAVEL_ABORT.value: self.travel["last_error"] = str(payload.get("reason") or "travel aborted")


class FakePlayer:
    def __init__(self, sock: socket.socket, args: argparse.Namespace) -> None:
        self.sock, self.args = sock, args
        self.state = FakePlayerState(args.player_id)
        self.manifest = CompatibilityManifest.local(args.player_id, args.name)
        self.stop, self.write_lock = threading.Event(), threading.Lock()
        self.pending_build: list[tuple[str, dict]] = []
        self.raw_watch = False
        self.save_receiver: SaveReceiver | None = None
        self.save_tmp = Path(tempfile.mkdtemp(prefix="kermp-fake-player-")) if args.accept_save_sync else None

    def send(self, typ: MessageType | str, payload: dict | None = None) -> str:
        payload = payload or {}; env = Envelope.make(typ, payload, self.args.player_id)
        with self.write_lock: self.sock.sendall(env.to_line())
        if payload.get("request_id"): self.state.last_request_id = payload["request_id"]
        return env.message_id

    def receiver(self, sock_file) -> None:
        try:
            while not self.stop.is_set():
                line = sock_file.readline()
                if not line:
                    if not self.stop.is_set(): print("Disconnected from host", flush=True)
                    self.state.connection = "disconnected"; return
                try: self.handle(Envelope.from_line(line))
                except Exception as exc: self.state.last_error = str(exc); print(f"receiver error: {exc}", flush=True)
        except (OSError, ValueError):
            if not self.stop.is_set(): print("Disconnected from host", flush=True)

    def handle(self, env: Envelope) -> None:
        typ, p = env.type, env.payload or {}
        if self.args.verbose: print(json.dumps({"type": typ, "payload": p}, ensure_ascii=False), flush=True)
        if typ == MessageType.WELCOME.value:
            self.state.connection, self.state.session_id = "connected", str(p.get("session_id") or "")
            self.state.compatibility = p.get("compatibility_reasons") or "compatible"; self.state.apply_snapshot(p.get("snapshot") or {}); return
        if typ in (MessageType.READINESS.value, MessageType.COMPATIBILITY.value):
            if p.get("player_id", self.args.player_id) == self.args.player_id: self.state.readiness = dict(p)
            if "compatibility_reasons" in p: self.state.compatibility = p["compatibility_reasons"] or "compatible"
        elif typ == MessageType.SNAPSHOT.value: self.state.apply_snapshot(p)
        elif typ == MessageType.CLOCK_STATE.value: self.state.clock = dict(p)
        elif typ == MessageType.BUILD_LOCK_STATE.value:
            self.state.build_lock_owner = p.get("owner_id")
            if self.state.build_lock_owner == self.args.player_id:
                queued, self.pending_build = self.pending_build[:], []
                for op, data in queued: self.send_build(op, data)
                print("[BUILD] lock granted", flush=True)
        elif typ in (MessageType.TRAVEL_PROPOSE.value, MessageType.TRAVEL_COMMIT.value, MessageType.TRAVEL_RESUME.value, MessageType.TRAVEL_ABORT.value, MessageType.TRAVEL_VIEW_BATCH.value):
            self.state.update_travel(typ, p); print(f"[TRAVEL] {typ.removeprefix('travel.')} epoch={p.get('epoch')} txn={p.get('txn_id')} zone={p.get('zone_id', '')}", flush=True)
            if typ == MessageType.TRAVEL_PROPOSE.value and self.args.auto_travel_ack: self.send(MessageType.TRAVEL_READY, {"txn_id": p.get("txn_id"), "epoch": p.get("epoch")})
            if typ == MessageType.TRAVEL_COMMIT.value and self.args.auto_travel_ack: self.send(MessageType.TRAVEL_ZONE_READY, {"txn_id": p.get("txn_id"), "epoch": p.get("epoch"), "zone_id": p.get("zone_id")})
        elif typ == MessageType.GAME_RAW_MESSAGE.value:
            self.state.raw_messages_received += 1; self.state.last_msg_id = p.get("msg_id"); self.state.last_size = len(base64.b64decode(str(p.get("payload_b64", "")), validate=True))
            if self.raw_watch: print(f"[RAW] msg_id={self.state.last_msg_id} size={self.state.last_size} sequence={p.get('sequence')} epoch={p.get('epoch')}", flush=True)
        elif typ == MessageType.DIALOG_OPEN.value:
            dialog_id = str(p.get("dialog_id") or "")
            if dialog_id: self.state.dialogs[dialog_id] = {"dialog_id": dialog_id, "dialog_type": p.get("dialog_type")}
            print(f"[DIALOG] {dialog_id} type={p.get('dialog_type')}", flush=True)
        elif typ == MessageType.ERROR.value: self.state.last_error = str(p.get("reason") or "unknown error"); print(f"error: {self.state.last_error}", flush=True)
        elif typ == MessageType.SAVE_MANIFEST.value: self.handle_save_manifest(p)
        elif typ == MessageType.SAVE_CHUNK.value: self.handle_save_chunk(p)
        elif typ == MessageType.SAVE_END.value: self.handle_save_end(p)
        elif typ in (MessageType.COMMAND_RESULT.value, MessageType.INTERACTION_ACCEPTED.value, MessageType.INTERACTION_REJECTED.value):
            self.state.last_result = p; self.state.last_request_id = p.get("request_id", self.state.last_request_id); print(f"[RESULT] {typ} request_id={p.get('request_id', '')}", flush=True)

    def handle_save_manifest(self, p: dict) -> None:
        if not self.args.accept_save_sync: print("[SAVE] manifest received (save sync disabled)", flush=True); return
        self.save_receiver = SaveReceiver(p, self.save_tmp); print(f"[SAVE] receiving {p.get('slot_id')} chunks={p.get('chunk_count')}", flush=True)

    def handle_save_chunk(self, p: dict) -> None:
        if not self.args.accept_save_sync: print(f"[SAVE] chunk {p.get('index')} received (not accepted)", flush=True); return
        try:
            if not self.save_receiver: raise ValueError("save_transfer_not_started")
            self.save_receiver.write_chunk(int(p.get("index", -1)), base64.b64decode(str(p.get("payload_b64", "")), validate=True))
        except Exception as exc:
            if self.save_receiver: self.save_receiver.abort()
            self.send(MessageType.SAVE_ERROR, {"reason": str(exc)})
            raise

    def handle_save_end(self, p: dict) -> None:
        if not self.args.accept_save_sync: print("[SAVE] end received (not accepted)", flush=True); return
        receiver = self.save_receiver
        try:
            if not receiver: raise ValueError("save_transfer_not_started")
            receiver.finalize(str(p.get("session_id") or "unknown"))
            self.send(MessageType.SAVE_ACK, {"slot_id": receiver.slot_id, "sha256": receiver.expected_hash, "total_bytes": receiver.total}); self.save_receiver = None
            print(f"[SAVE] accepted sha256={receiver.expected_hash}", flush=True)
        except Exception as exc:
            if receiver: receiver.abort()
            self.send(MessageType.SAVE_ERROR, {"reason": str(exc)})
            raise

    def send_build(self, op: str, data: dict) -> None:
        if self.state.build_lock_owner != self.args.player_id:
            self.pending_build.append((op, data)); self.send(MessageType.BUILD_LOCK_REQUEST); print("→ build queued, requesting lock", flush=True); return
        self.send(MessageType.BUILD_OPERATION, {"op": op, "data": data}); print(f"[BUILD] sent {op} object={data.get('object_id', '')}", flush=True)

    def run_command(self, line: str) -> bool:
        command, a = parse_command(line); self.state.last_command = line.strip()
        try:
            if not command: return True
            if command == "help": self.print_help()
            elif command == "quit": return False
            elif command == "manifest": print(json.dumps(self.manifest.to_dict(), indent=2), flush=True)
            elif command == "status": self.print_status()
            elif command == "travel": self.require(a, 1); self.send(MessageType.TRAVEL_REQUEST, {"zone_id": a[0], "actor_ids": []}); print(f"→ sent travel.request zone={a[0]}", flush=True)
            elif command == "travel-status": print(json.dumps(self.state.travel, indent=2), flush=True)
            elif command == "build-lock": self.send(MessageType.BUILD_LOCK_REQUEST)
            elif command == "build-release": self.state.build_lock_owner = None; self.send(MessageType.BUILD_LOCK_RELEASE)
            elif command in ("buy", "recolor", "scale", "sell", "move", "clear-parent", "parent"): self.build_command(command, a)
            elif command == "select": self.require(a, 1); self.state.selected_sim = a[0]; self.send(MessageType.SIM_SELECT, {"sim_id": a[0]})
            elif command == "interact":
                if len(a) not in (1, 2): raise ValueError("usage: interact <affordance_id> [target_id]")
                rid = uuid.uuid4().hex; self.send(MessageType.INTERACTION_REQUEST, {"request_id": rid, "sim_id": self.state.selected_sim, "affordance_id": a[0], "target_id": a[1] if len(a) == 2 else None})
            elif command == "cancel": self.require(a, 1); self.send(MessageType.INTERACTION_CANCEL, {"interaction_id": a[0], "request_id": a[0]})
            elif command == "choices": print("unsupported by current protocol", flush=True)
            elif command in ("pause", "resume"): self.send(MessageType.CLOCK_REQUEST_PAUSE, {"paused": command == "pause"})
            elif command == "speed": self.require(a, 1); speed = int(a[0]); self.require_value(speed in (0, 1, 2, 3), "speed must be 0, 1, 2, or 3"); self.send(MessageType.CLOCK_REQUEST_SPEED, {"speed": speed})
            elif command == "dialogs": self.print_dialogs()
            elif command == "dialog": self.dialog_command(a)
            elif command == "raw-status": print(f"count={self.state.raw_messages_received} last_msg_id={self.state.last_msg_id} last_size={self.state.last_size}", flush=True)
            elif command == "raw-watch": self.require(a, 1); self.raw_watch = a[0].lower() == "on"; print(f"raw-watch {'on' if self.raw_watch else 'off'}", flush=True)
            elif command == "snapshot": self.send(MessageType.SNAPSHOT_REQUEST)
            elif command == "last": print(json.dumps({"last_command": self.state.last_command, "request_id": self.state.last_request_id, "last_correlated_result": self.state.last_result, "last_error": self.state.last_error}, indent=2), flush=True)
            elif command == "scenario": self.scenario(a)
            else: print("bad command; type help", flush=True)
        except (ValueError, TypeError) as exc: self.state.last_error = str(exc); print(f"error: {exc}", flush=True)
        return True

    @staticmethod
    def require(values: list[str], count: int) -> None:
        if len(values) < count: raise ValueError("missing arguments")
    @staticmethod
    def require_value(condition: bool, message: str) -> None:
        if not condition: raise ValueError(message)

    def build_command(self, command: str, a: list[str]) -> None:
        self.require(a, 1); oid = a[0]
        if command == "move": op, data = "object.move", move_payload(oid, a[1:])
        elif command == "buy": self.require(a, 2); op, data = "object.create", {"object_id": oid, "definition_id": a[1]}
        elif command == "recolor": self.require(a, 2); op, data = "object.definition", {"object_id": oid, "definition_id": a[1]}
        elif command == "scale": self.require(a, 2); op, data = "object.scale", {"object_id": oid, "scale": float(a[1])}
        elif command == "parent": self.require(a, 2); op, data = "object.set_parent", {"object_id": oid, "parent_id": a[1]}
        elif command == "clear-parent": op, data = "object.clear_parent", {"object_id": oid}
        else: op, data = "object.destroy", {"object_id": oid}
        self.send_build(op, data)

    def scenario(self, a: list[str]) -> None:
        self.require(a, 1)
        if a[0] == "travel": self.require(a, 2); self.run_command(f"travel {a[1]}")
        elif a[0] in ("build-smoke", "build-destructive"):
            self.require(a, 3)
            if a[0] == "build-destructive": print("WARNING: sending destructive build sequence", flush=True)
            for command in (f"recolor {a[1]} {a[2]}", f"move {a[1]} 0 0 0 0 0 0 1", f"scale {a[1]} 1"): self.run_command(command)
            if a[0] == "build-destructive": self.run_command(f"buy {a[1]} {a[2]}"); self.run_command(f"sell {a[1]}")
        else: raise ValueError("unknown scenario")

    def print_help(self) -> None:
        print("help | status | manifest | select <sim_id> | interact <affordance_id> [target_id] | cancel <interaction_id> | choices <target_id> | travel <zone_id> | travel-status | build-lock | build-release | buy <object_id> <definition_id> | move <object_id> <x> <y> <z> <qx> <qy> <qz> <qw> | recolor <object_id> <definition_id> | scale <object_id> <scale> | clear-parent <object_id> | parent <object_id> <parent_id> | sell <object_id> | pause | resume | speed <0-3> | dialogs | dialog <id> yes|no|select <value> | raw-status | raw-watch on|off | scenario travel|build-smoke|build-destructive ... | last | snapshot | quit", flush=True)

    def print_status(self) -> None:
        print(json.dumps({"player_id": self.args.player_id, "connection": self.state.connection, "session_id": self.state.session_id, "compatibility": self.state.compatibility, "readiness": self.state.readiness, "save_progress": self.state.readiness.get("save_progress"), "bridge_readiness": self.state.readiness.get("bridge_connected"), "zone": self.state.zone, "clock": self.state.clock, "build_lock_owner": self.state.build_lock_owner, "travel": self.state.travel, "last_error": self.state.last_error}, indent=2), flush=True)

    def print_dialogs(self) -> None: print(json.dumps(list(self.state.dialogs.values()), indent=2), flush=True)

    def dialog_command(self, a: list[str]) -> None:
        self.require(a, 2); choice = a[1].lower()
        if a[0] not in self.state.dialogs: raise ValueError("unknown_or_completed_dialog")
        if choice in ("yes", "no"): response = {"accepted": choice == "yes"}
        elif choice == "select" and len(a) >= 3: response = {"value": " ".join(a[2:])}
        else: raise ValueError("usage: dialog <dialog_id> yes|no|select <value>")
        self.send(MessageType.DIALOG_RESPONSE, {"dialog_id": a[0], "response": response})
        self.state.dialogs.pop(a[0], None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kermp-fake-player")
    ap.add_argument("host"); ap.add_argument("--port", type=int, default=17653); ap.add_argument("--name", default="Player2"); ap.add_argument("--player-id", default="player2")
    ap.add_argument("--auto-travel-ack", action="store_true"); ap.add_argument("--accept-save-sync", action="store_true"); ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    try:
        with socket.create_connection((args.host, args.port)) as sock:
            client = FakePlayer(sock, args); client.send(MessageType.HELLO, {"display_name": args.name, "manifest": client.manifest.to_dict()})
            sock_file = sock.makefile("rb"); first = sock_file.readline()
            if first: client.handle(Envelope.from_line(first))
            thread = threading.Thread(target=client.receiver, args=(sock_file,), daemon=True); thread.start(); client.print_help()
            while not client.stop.is_set():
                try: line = input("player2> ")
                except EOFError: break
                if not client.run_command(line): break
            client.stop.set(); sock.shutdown(socket.SHUT_RDWR); thread.join(timeout=1)
    except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
        print(f"connection failed: {exc}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
