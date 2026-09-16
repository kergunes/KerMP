from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from .protocol import Envelope, MessageType
from .session import HostSession

MessageHandler = Callable[[Envelope], Awaitable[None]]


@dataclass
class Peer:
    player_id: str
    display_name: str
    writer: asyncio.StreamWriter
    last_seq: int = 0


class KerMPHost:
    def __init__(self, player_id: str, display_name: str, host: str = "0.0.0.0", port: int = 17653) -> None:
        self.player_id = player_id
        self.display_name = display_name
        self.host = host
        self.port = port
        self.session = HostSession()
        self.session.add_player(player_id, display_name)
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
            self.peers[player_id] = Peer(player_id, name, writer)
            self.session.add_player(player_id, name)
            writer.write(Envelope.make(
                MessageType.WELCOME,
                {"session_name": self.session.session_name, "session_id": self.session.session_id,
                 "host_id": self.player_id, "players": [p.display_name for p in self.session.players.values()]},
                self.player_id,
            ).to_line())
            await writer.drain()

            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                env = Envelope.from_line(line)
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

    async def _drop(self, player_id: str) -> None:
        peer = self.peers.pop(player_id, None)
        self.session.remove_player(player_id)
        if peer:
            with contextlib.suppress(Exception):
                peer.writer.close()
                await peer.writer.wait_closed()


class KerMPClient:
    def __init__(self, player_id: str, display_name: str, host: str, port: int = 17653) -> None:
        self.player_id = player_id
        self.display_name = display_name
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.on_message: Optional[MessageHandler] = None

    async def connect(self) -> Envelope:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.writer.write(Envelope.make(MessageType.HELLO, {"display_name": self.display_name}, self.player_id).to_line())
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
