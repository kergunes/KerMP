from __future__ import annotations

import asyncio
import base64
import contextlib
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from .compatibility import CompatibilityManifest
from .protocol import Envelope, MessageType
from .session import HostSession
from .save_sync import SaveSlot

MessageHandler = Callable[[Envelope], Awaitable[None]]


@dataclass
class Peer:
    player_id: str
    display_name: str
    writer: asyncio.StreamWriter
    last_seq: int = 0


class KerMPHost:
    def __init__(self, player_id: str, display_name: str, host: str = "0.0.0.0", port: int = 17653,
                 *, manifest: CompatibilityManifest | None = None, native_build_buy_required: bool = False) -> None:
        self.player_id = player_id
        self.display_name = display_name
        self.host = host
        self.port = port
        self.manifest = manifest or CompatibilityManifest.local(player_id, display_name)
        self.session = HostSession(host_manifest=self.manifest, native_build_buy_required=native_build_buy_required)
        self.session.add_player(player_id, display_name, self.manifest)
        self.session.update_readiness(player_id, save_hash="host-local", bridge_connected=False,
                                      zone_ready=False, simulation_authority_ready=True)
        self.peers: Dict[str, Peer] = {}
        self._server: asyncio.AbstractServer | None = None
        self._seq = 0
        self.on_message: Optional[MessageHandler] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_conn, self.host, self.port)

    async def serve_forever(self) -> None:
        if not self._server:
            await self.start()
        assert self._server
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        for peer in list(self.peers.values()):
            peer.writer.close()
            with contextlib.suppress(Exception):
                await peer.writer.wait_closed()
        self.peers.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def broadcast(self, typ: MessageType | str, payload: dict, include_host: bool = False) -> Envelope:
        self._seq += 1
        env = Envelope.make(typ, payload, self.player_id, seq=self._seq)
        dead = []
        for pid, peer in self.peers.items():
            try:
                peer.writer.write(env.to_line())
                await peer.writer.drain()
            except OSError:
                dead.append(pid)
        for pid in dead:
            await self._drop(pid)
        if include_host and self.on_message:
            await self.on_message(env)
        return env

    async def send_to(self, player_id: str, typ: MessageType | str, payload: dict) -> None:
        peer = self.peers.get(player_id)
        if not peer: raise ValueError("unknown_player")
        peer.writer.write(Envelope.make(typ, payload, self.player_id).to_line())
        await peer.writer.drain()

    async def stream_save_to(self, player_id: str, slot: SaveSlot, *, chunk_size: int = 64 * 1024) -> None:
        manifest = slot.manifest(self.session.session_id, chunk_size)
        await self.send_to(player_id, MessageType.SAVE_MANIFEST, manifest)
        await self.send_to(player_id, MessageType.SAVE_BEGIN, {"session_id": self.session.session_id, "slot_id": slot.slot_id})
        with slot.path.open("rb") as stream:
            for index in range(manifest["chunk_count"]):
                raw = stream.read(chunk_size)
                await self.send_to(player_id, MessageType.SAVE_CHUNK, {"session_id": self.session.session_id, "index": index,
                    "payload_b64": base64.b64encode(raw).decode("ascii")})
        await self.send_to(player_id, MessageType.SAVE_END, {"session_id": self.session.session_id, "sha256": slot.sha256})

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        player_id = None
        try:
            hello = Envelope.from_line(await asyncio.wait_for(reader.readline(), timeout=8))
            if hello.type != MessageType.HELLO.value:
                raise ValueError("first message must be hello")
            player_id = hello.sender_id
            name = str(hello.payload.get("display_name") or "Player")
            if player_id in self.peers or player_id == self.player_id:
                writer.write(Envelope.make(MessageType.ERROR, {"reason": "duplicate_player_id"}, self.player_id).to_line())
                await writer.drain()
                return
            try:
                manifest = CompatibilityManifest.from_dict(dict(hello.payload.get("manifest") or {}))
                if manifest.player_id != player_id:
                    raise ValueError("manifest_player_id_mismatch")
            except (TypeError, ValueError) as exc:
                writer.write(Envelope.make(MessageType.ERROR, {"reason": "invalid_manifest:%s" % exc}, self.player_id).to_line())
                await writer.drain(); return
            self.peers[player_id] = Peer(player_id, name, writer)
            self.session.add_player(player_id, name, manifest)
            writer.write(Envelope.make(
                MessageType.WELCOME,
                {"session_name": self.session.session_name, "session_id": self.session.session_id,
                 "host_id": self.player_id, "players": [p.display_name for p in self.session.players.values()],
                 "snapshot": self.session.snapshot(), "host_manifest": self.manifest.to_dict(),
                 "compatibility_reasons": list(self.session.players[player_id].readiness.compatibility_reasons)},
                self.player_id,
            ).to_line())
            await writer.drain()

            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                env = Envelope.from_line(line)
                if env.sender_id != player_id:
                    await self._send_error(player_id, 'sender_id_mismatch')
                    continue
                peer = self.peers.get(player_id)
                if peer and env.seq is not None and env.seq <= peer.last_seq:
                    continue
                if peer and env.seq is not None:
                    peer.last_seq = env.seq
                await self._dispatch(env)
        except (asyncio.TimeoutError, ValueError, ConnectionError, OSError):
            pass
        finally:
            if player_id:
                await self._drop(player_id)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, env: Envelope) -> None:
        if env.type == MessageType.PING.value:
            peer = self.peers.get(env.sender_id)
            if peer:
                peer.writer.write(Envelope.make(MessageType.PONG, {"echo": env.message_id}, self.player_id).to_line())
                await peer.writer.drain()
            return

        if env.type == MessageType.READINESS.value:
            try:
                allowed = {"save_bytes_received", "save_total_bytes", "save_hash", "bridge_connected", "zone_ready", "simulation_authority_ready", "error"}
                fields = {key: value for key, value in env.payload.items() if key in allowed}
                readiness = self.session.update_readiness(env.sender_id, **fields)
                await self.broadcast(MessageType.READINESS, {"player_id": env.sender_id, **readiness.snapshot()}, include_host=True)
            except (ValueError, KeyError) as exc:
                await self._send_error(env.sender_id, str(exc))
            return
        if env.type == MessageType.SAVE_ACK.value:
            try:
                readiness = self.session.update_readiness(env.sender_id, save_hash=str(env.payload.get("sha256") or ""),
                                                          save_bytes_received=int(env.payload.get("total_bytes", 0)),
                                                          save_total_bytes=int(env.payload.get("total_bytes", 0)))
                await self.broadcast(MessageType.READINESS, {"player_id": env.sender_id, **readiness.snapshot()}, include_host=True)
            except (ValueError, KeyError) as exc: await self._send_error(env.sender_id, str(exc))
            return
        if env.type == MessageType.SAVE_ERROR.value:
            self.session.update_readiness(env.sender_id, error="save_sync:" + str(env.payload.get("reason", "unknown")))
            await self.broadcast(MessageType.READINESS, {"player_id": env.sender_id, **self.session.players[env.sender_id].readiness.snapshot()}, include_host=True)
            return
        if env.type == MessageType.CLOCK_REQUEST_SPEED.value:
            speed = int(env.payload.get("speed", 0))
            if speed not in (0, 1, 2, 3):
                await self._send_error(env.sender_id, "invalid_clock_speed"); return
            self.session.clock.update(sequence=self.session.clock["sequence"] + 1, speed=speed, paused=(speed == 0))
            await self.broadcast(MessageType.CLOCK_STATE, dict(self.session.clock), include_host=True); return
        if env.type == MessageType.CLOCK_REQUEST_PAUSE.value:
            paused = bool(env.payload.get("paused"))
            self.session.clock.update(sequence=self.session.clock["sequence"] + 1, paused=paused, speed=(0 if paused else max(1, int(self.session.clock["speed"]))))
            await self.broadcast(MessageType.CLOCK_STATE, dict(self.session.clock), include_host=True); return
        if env.type == MessageType.CLOCK_RESYNC.value:
            peer = self.peers.get(env.sender_id)
            if peer:
                peer.writer.write(Envelope.make(MessageType.CLOCK_STATE, dict(self.session.clock), self.player_id).to_line()); await peer.writer.drain()
            return
        if env.type == MessageType.COMMAND_REQUEST.value:
            try:
                result = self.session.commands.dispatch(env.sender_id, str(env.payload.get("family")), dict(env.payload.get("payload") or {}), env.payload.get("request_id"))
                await self.broadcast(MessageType.COMMAND_RESULT, result, include_host=True)
            except ValueError as exc: await self._send_error(env.sender_id, str(exc))
            return
        if env.type == MessageType.DIALOG_RESPONSE.value:
            try:
                result = self.session.dialogs.respond(env.sender_id, str(env.payload.get("dialog_id")), dict(env.payload.get("response") or {}))
                await self.broadcast(MessageType.DIALOG_RESPONSE, result, include_host=True)
            except ValueError as exc: await self._send_error(env.sender_id, str(exc))
            return

        if env.type == MessageType.SNAPSHOT_REQUEST.value:
            peer = self.peers.get(env.sender_id)
            if peer:
                snapshot = self.session.snapshot()
                peer.writer.write(Envelope.make(MessageType.SNAPSHOT, snapshot, self.player_id, seq=self._seq).to_line())
                await peer.writer.drain()
            return

        if env.type == MessageType.BUILD_LOCK_REQUEST.value:
            granted = self.session.build.request_lock(env.sender_id)
            await self.broadcast(MessageType.BUILD_LOCK_STATE, {"owner_id": self.session.build.lock.owner_id if self.session.build.lock else None, "granted_to": env.sender_id if granted else None})
            return
        if env.type == MessageType.SIM_SELECT.value:
            try:
                state = self.session.select_sim(env.sender_id, str(env.payload.get("sim_id")))
            except ValueError as exc:
                await self._send_error(env.sender_id, str(exc))
                return
            await self.broadcast(MessageType.SIM_SELECTION_STATE, state, include_host=True)
            return
        if env.type == MessageType.SIM_STATE.value:
            self.session.update_sims(list(env.payload.get("sims") or []))
            await self.broadcast(MessageType.SIM_STATE, self.session.snapshot(), include_host=True)
            return
        if env.type == MessageType.INTERACTION_REQUEST.value:
            try:
                request = self.session.validate_interaction(env.payload.get("request_id"), env.sender_id, env.payload)
            except ValueError as exc:
                await self._send_error(env.sender_id, str(exc))
                return
            await self.broadcast(MessageType.INTERACTION_ACCEPTED, request, include_host=True)
            return
        if env.type == MessageType.BUILD_LOCK_RELEASE.value:
            self.session.build.release_lock(env.sender_id)
            await self.broadcast(MessageType.BUILD_LOCK_STATE, {"owner_id": None})
            return
        if env.type == MessageType.BUILD_OPERATION.value:
            try:
                op = self.session.build.submit(env.sender_id, str(env.payload["op"]), dict(env.payload.get("data", {})))
            except (PermissionError, KeyError, TypeError) as exc:
                peer = self.peers.get(env.sender_id)
                if peer:
                    peer.writer.write(Envelope.make(MessageType.ERROR, {"reason": str(exc)}, self.player_id).to_line())
                    await peer.writer.drain()
                return
            await self.broadcast(MessageType.BUILD_APPLY, {"op_seq": op.seq, "player_id": op.player_id, "op": op.op, "data": op.data}, include_host=True)
            return

        if self.on_message:
            await self.on_message(env)

    async def _send_error(self, player_id: str, reason: str) -> None:
        peer = self.peers.get(player_id)
        if peer:
            peer.writer.write(Envelope.make(MessageType.ERROR, {"reason": reason}, self.player_id).to_line())
            await peer.writer.drain()

    async def _drop(self, player_id: str) -> None:
        peer = self.peers.pop(player_id, None)
        self.session.remove_player(player_id)
        if peer:
            with contextlib.suppress(Exception):
                peer.writer.close()
                await peer.writer.wait_closed()


class KerMPClient:
    def __init__(self, player_id: str, display_name: str, host: str, port: int = 17653,
                 *, manifest: CompatibilityManifest | None = None) -> None:
        self.player_id = player_id
        self.display_name = display_name
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.on_message: Optional[MessageHandler] = None
        self.manifest = manifest or CompatibilityManifest.local(player_id, display_name)

    async def connect(self) -> Envelope:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.writer.write(Envelope.make(MessageType.HELLO, {"display_name": self.display_name, "manifest": self.manifest.to_dict()}, self.player_id).to_line())
        await self.writer.drain()
        response = Envelope.from_line(await asyncio.wait_for(self.reader.readline(), timeout=8))
        if response.type == MessageType.ERROR.value:
            raise RuntimeError(response.payload.get("reason", "host rejected connection"))
        if response.type != MessageType.WELCOME.value:
            raise RuntimeError("unexpected handshake response")
        return response

    async def send(self, typ: MessageType | str, payload: dict | None = None) -> None:
        if not self.writer:
            raise RuntimeError("not connected")
        self.writer.write(Envelope.make(typ, payload or {}, self.player_id).to_line())
        await self.writer.drain()

    async def listen(self) -> None:
        if not self.reader:
            raise RuntimeError("not connected")
        while not self.reader.at_eof():
            line = await self.reader.readline()
            if not line:
                break
            env = Envelope.from_line(line)
            if self.on_message:
                await self.on_message(env)

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
        self.reader = None
        self.writer = None
