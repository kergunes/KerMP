from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque, Optional

from .bridge import BridgeEvent, LocalGameBridge
from .net import KerMPHost, KerMPClient
from .protocol import Envelope, MessageType


class HostRuntime:
    """Glue between one local Sims instance and the authoritative LAN host."""

    def __init__(self, host: KerMPHost, bridge_port: int = 17654) -> None:
        self.host = host
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bridge = LocalGameBridge(bridge_port, self._bridge_event_from_thread)
        self.host.on_message = self._on_network_message

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.bridge.start()
        await self.host.start()

    def _bridge_event_from_thread(self, event: BridgeEvent) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._on_game_event(event), self.loop)

    async def _on_game_event(self, event: BridgeEvent) -> None:
        if event.type == "game.hello":
            self.bridge.send("sidecar.welcome", {
                "role": "host",
                "player_id": self.host.player_id,
                "protocol_version": 1,
            })
            return

        if event.type == MessageType.SIM_SELECT.value:
            try:
                state = self.host.session.select_sim(self.host.player_id, str(event.payload.get('sim_id')))
            except ValueError as exc:
                self.bridge.send(MessageType.ERROR.value, {'reason': str(exc)})
                return
            await self.host.broadcast(MessageType.SIM_SELECTION_STATE, state, include_host=True)
            return

        if event.type == MessageType.INTERACTION_REQUEST.value:
            payload = dict(event.payload)
            payload.setdefault('player_id', self.host.player_id)
            try:
                request = self.host.session.validate_interaction(payload.get('request_id'), self.host.player_id, payload)
            except ValueError as exc:
                self.bridge.send(MessageType.INTERACTION_REJECTED.value, {'request_id': payload.get('request_id'), 'reason': str(exc)})
                return
            self.bridge.send(MessageType.INTERACTION_REQUEST.value, request)
            await self.host.broadcast(MessageType.INTERACTION_ACCEPTED, request)
            return

        if event.type == MessageType.TRAVEL_REQUEST.value:
            await self._start_travel(event.payload, requested_by=self.host.player_id)
            return

        if event.type == MessageType.TRAVEL_READY.value:
            await self._travel_ready(self.host.player_id, event.payload)
            return

        if event.type == MessageType.TRAVEL_ZONE_READY.value:
            await self._travel_zone_ready(self.host.player_id, event.payload)
            return

        if event.type == MessageType.BUILD_OPERATION.value:
            if not self.host.session.build.request_lock(self.host.player_id):
                self.bridge.send("build.lock_state", {"owner_id": self.host.session.build.lock.owner_id})
                return
            op = self.host.session.build.submit(
                self.host.player_id,
                str(event.payload.get("op")),
                dict(event.payload.get("data") or {}),
            )
            payload = {"op_seq": op.seq, "player_id": op.player_id, "op": op.op, "data": op.data}
            self.bridge.send(MessageType.BUILD_APPLY.value, payload)
            await self.host.broadcast(MessageType.BUILD_APPLY, payload)
            return

        if event.type == MessageType.BUILD_LOCK_REQUEST.value:
            granted = self.host.session.build.request_lock(self.host.player_id)
            payload = {"owner_id": self.host.session.build.lock.owner_id if self.host.session.build.lock else None,
                       "granted_to": self.host.player_id if granted else None}
            self.bridge.send(MessageType.BUILD_LOCK_STATE.value, payload)
            await self.host.broadcast(MessageType.BUILD_LOCK_STATE, payload)
            return

        if event.type == MessageType.BUILD_LOCK_RELEASE.value:
            self.host.session.build.release_lock(self.host.player_id)
            payload = {"owner_id": self.host.session.build.lock.owner_id if self.host.session.build.lock else None}
            self.bridge.send(MessageType.BUILD_LOCK_STATE.value, payload)
            await self.host.broadcast(MessageType.BUILD_LOCK_STATE, payload)
            return
        if event.type in ("sims.state", "sim.state"):
            self.host.session.update_sims(event.payload.get("sims", []))
            await self.host.broadcast(MessageType.SIM_STATE, self.host.session.snapshot())
            return
        if event.type in (MessageType.INTERACTION_ACCEPTED.value, MessageType.INTERACTION_REJECTED.value,
                          MessageType.INTERACTION_STARTED.value, MessageType.INTERACTION_FINISHED.value):
            request_id = str(event.payload.get("request_id") or "")
            if request_id in self.host.session.interaction_requests:
                self.host.session.interaction_requests[request_id]["status"] = event.type.rsplit('.', 1)[-1]
            await self.host.broadcast(event.type, event.payload)

    async def _on_network_message(self, env: Envelope) -> None:
        if env.type == MessageType.TRAVEL_REQUEST.value:
            await self._start_travel(env.payload, requested_by=env.sender_id)
        elif env.type == MessageType.TRAVEL_READY.value:
            await self._travel_ready(env.sender_id, env.payload)
        elif env.type == MessageType.TRAVEL_ZONE_READY.value:
            await self._travel_zone_ready(env.sender_id, env.payload)
        elif env.type == MessageType.BUILD_APPLY.value:
            self.bridge.send(MessageType.BUILD_APPLY.value, env.payload)
        elif env.type in (MessageType.SIM_SELECTION_STATE.value, MessageType.SIM_STATE.value):
            self.bridge.send(env.type, env.payload)
        elif env.type == MessageType.INTERACTION_ACCEPTED.value:
            self.bridge.send(MessageType.INTERACTION_REQUEST.value, env.payload)
        elif env.type in (MessageType.INTERACTION_REJECTED.value, MessageType.INTERACTION_STARTED.value,
                          MessageType.INTERACTION_FINISHED.value, MessageType.ERROR.value):
            self.bridge.send(env.type, env.payload)

    async def _start_travel(self, payload: dict, requested_by: str) -> None:
        zone_id = str(payload.get("zone_id") or "")
        actor_ids = [str(x) for x in payload.get("actor_ids", [])]
        if not zone_id:
            return
        participants = list(self.host.session.players.keys())
        txn = self.host.session.travel.propose(zone_id, actor_ids, participants)
        out = {
            "txn_id": txn.txn_id,
            "zone_id": txn.zone_id,
            "actor_ids": txn.actor_ids,
            "requested_by": requested_by,
        }
        self.bridge.send("travel.prepare", out)
        await self.host.broadcast(MessageType.TRAVEL_PROPOSE, out)

    async def _travel_ready(self, player_id: str, payload: dict) -> None:
        txn_id = str(payload.get("txn_id") or "")
        if not txn_id:
            return
        try:
            commit = self.host.session.travel.mark_ready(player_id, txn_id)
        except KeyError:
            return
        if commit:
            txn = self.host.session.travel.current
            assert txn is not None
            out = {"txn_id": txn.txn_id, "zone_id": txn.zone_id, "actor_ids": txn.actor_ids}
            self.bridge.send(MessageType.TRAVEL_COMMIT.value, out)
            await self.host.broadcast(MessageType.TRAVEL_COMMIT, out)
            self.host.session.travel.begin_zone_wait(txn_id)

    async def _travel_zone_ready(self, player_id: str, payload: dict) -> None:
        txn_id = str(payload.get("txn_id") or "")
        if not txn_id:
            return
        try:
            resume = self.host.session.travel.mark_zone_ready(player_id, txn_id)
        except KeyError:
            return
        if resume:
            out = {"txn_id": txn_id}
            self.bridge.send(MessageType.TRAVEL_RESUME.value, out)
            await self.host.broadcast(MessageType.TRAVEL_RESUME, out)


class ClientRuntime:
    """Glue between one local Sims instance and a remote KerMP host."""

    def __init__(self, client: KerMPClient, bridge_port: int = 17654) -> None:
        self.client = client
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bridge = LocalGameBridge(bridge_port, self._bridge_event_from_thread)
        self.client.on_message = self._on_network_message
        self.pending_build: Deque[dict] = deque()
        self.owns_build_lock = False

    async def start(self) -> Envelope:
        self.loop = asyncio.get_running_loop()
        self.bridge.start()
        return await self.client.connect()

    def _bridge_event_from_thread(self, event: BridgeEvent) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._on_game_event(event), self.loop)

    async def _on_game_event(self, event: BridgeEvent) -> None:
        if event.type == "game.hello":
            self.bridge.send("sidecar.welcome", {
                "role": "client",
                "player_id": self.client.player_id,
                "protocol_version": 1,
            })
            return
        if event.type == MessageType.TRAVEL_REQUEST.value:
            await self.client.send(MessageType.TRAVEL_REQUEST, event.payload)
            return
        if event.type == MessageType.SIM_SELECT.value:
            await self.client.send(MessageType.SIM_SELECT, event.payload)
            return
        if event.type == MessageType.INTERACTION_REQUEST.value:
            await self.client.send(MessageType.INTERACTION_REQUEST, event.payload)
            return
        if event.type in (MessageType.TRAVEL_READY.value, MessageType.TRAVEL_ZONE_READY.value):
            await self.client.send(event.type, event.payload)
            return
        if event.type == MessageType.BUILD_OPERATION.value:
            self.pending_build.append(event.payload)
            if self.owns_build_lock:
                await self._flush_build()
            else:
                await self.client.send(MessageType.BUILD_LOCK_REQUEST)
            return
        if event.type == MessageType.BUILD_LOCK_REQUEST.value:
            await self.client.send(MessageType.BUILD_LOCK_REQUEST)
            return
        if event.type == MessageType.BUILD_LOCK_RELEASE.value:
            self.owns_build_lock = False
            await self.client.send(MessageType.BUILD_LOCK_RELEASE)

    async def _on_network_message(self, env: Envelope) -> None:
        if env.type == MessageType.TRAVEL_PROPOSE.value:
            self.bridge.send("travel.prepare", env.payload)
        elif env.type in (MessageType.TRAVEL_COMMIT.value, MessageType.TRAVEL_RESUME.value, MessageType.TRAVEL_ABORT.value):
            self.bridge.send(env.type, env.payload)
        elif env.type == MessageType.BUILD_LOCK_STATE.value:
            owner = env.payload.get("owner_id")
            self.owns_build_lock = owner == self.client.player_id
            self.bridge.send(MessageType.BUILD_LOCK_STATE.value, env.payload)
            if self.owns_build_lock:
                await self._flush_build()
        elif env.type == MessageType.BUILD_APPLY.value:
            self.bridge.send(MessageType.BUILD_APPLY.value, env.payload)
        elif env.type == MessageType.ERROR.value:
            self.bridge.send("sidecar.error", env.payload)

    async def _flush_build(self) -> None:
        while self.pending_build and self.owns_build_lock:
            payload = self.pending_build.popleft()
            await self.client.send(MessageType.BUILD_OPERATION, payload)
