from __future__ import annotations

import asyncio
import base64
from collections import deque
from typing import Deque, Optional

from .bridge import BridgeEvent, LocalGameBridge
from .net import KerMPHost, KerMPClient
from .protocol import Envelope, MessageType, MAX_GAME_MESSAGE_BYTES
from .save_sync import SaveReceiver, resolve_sims_user_dir


class HostRuntime:
    """Glue between one local Sims instance and the authoritative LAN host."""

    def __init__(self, host: KerMPHost, bridge_port: int = 17654) -> None:
        self.host = host
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bridge = LocalGameBridge(bridge_port, self._bridge_event_from_thread)
        self.host.on_message = self._on_network_message
        self.view_updates_sent = 0
        self.last_view_update_size = 0
        self.last_view_update_msg_id = None
        self._game_sequence = 0
        self.travel_batch_open = False

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.bridge.start()
        await self.host.start()

    async def stop(self) -> None:
        self.bridge.close()
        await self.host.close()

    def _bridge_event_from_thread(self, event: BridgeEvent) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._on_game_event(event), self.loop)

    async def _on_game_event(self, event: BridgeEvent) -> None:
        if event.type == "game.hello":
            readiness = self.host.session.update_readiness(self.host.player_id, bridge_connected=True)
            self.bridge.send("sidecar.welcome", {
                "role": "host",
                "player_id": self.host.player_id,
                "protocol_version": 1,
            })
            await self.host.broadcast(MessageType.READINESS, {"player_id": self.host.player_id, **readiness.snapshot()})
            return
        if event.type == "simulation.authority":
            readiness = self.host.session.update_readiness(self.host.player_id, bridge_connected=True,
                                                           simulation_authority_ready=True)
            await self.host.broadcast(MessageType.READINESS, {"player_id": self.host.player_id, **readiness.snapshot()})
            return

        if event.type == MessageType.GAME_RAW_MESSAGE.value:
            try:
                msg_id = int(event.payload['msg_id'])
                raw = base64.b64decode(str(event.payload['payload_b64']), validate=True)
                if len(raw) > MAX_GAME_MESSAGE_BYTES:
                    raise ValueError('payload_too_large')
            except Exception as exc:
                self.bridge.send(MessageType.ERROR.value, {'reason': 'raw_message_invalid:%s' % exc})
                return
            self._game_sequence += 1
            active = self.host.session.travel.current
            epoch = active.epoch if active and active.phase.value not in ('complete', 'aborted') else None
            payload = {'msg_id': msg_id, 'sequence': self._game_sequence,
                       'payload_b64': base64.b64encode(raw).decode('ascii')}
            if epoch is not None:
                payload['epoch'] = epoch
            self.view_updates_sent += 1
            self.last_view_update_size = len(raw)
            self.last_view_update_msg_id = msg_id
            await self.host.broadcast(MessageType.GAME_RAW_MESSAGE, payload)
            return

        if event.type == MessageType.TRAVEL_VIEW_BATCH.value:
            epoch = int(event.payload.get('epoch', 0))
            active = self.host.session.travel.current
            if not active or active.epoch != epoch:
                return
            kind = str(event.payload.get('kind', '')).lower()
            self.travel_batch_open = kind == 'begin'
            out = {'txn_id': active.txn_id, 'epoch': epoch, 'kind': kind}
            if kind == 'begin':
                self.bridge.send(MessageType.TRAVEL_VIEW_BATCH.value, out)
            await self.host.broadcast(MessageType.TRAVEL_VIEW_BATCH, out)
            if kind == 'end':
                self.bridge.send(MessageType.TRAVEL_VIEW_BATCH.value, out)
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
        if event.type == MessageType.TRAVEL_ABORT.value:
            txn = self.host.session.travel.current
            if txn and int(event.payload.get('epoch', -1)) == txn.epoch:
                self.host.session.travel.abort(txn.txn_id)
                await self.host.broadcast(MessageType.TRAVEL_ABORT, {
                    'txn_id': txn.txn_id, 'epoch': txn.epoch,
                    'reason': event.payload.get('reason', 'game_abort')})
            return

        if event.type == MessageType.BUILD_OPERATION.value:
            if not self.host.session.build.request_lock(self.host.player_id):
                self.bridge.send("build.lock_state", {"owner_id": self.host.session.build.lock.owner_id})
                return
            try:
                op = self.host.session.build.submit(
                    self.host.player_id,
                    str(event.payload.get("op")),
                    dict(event.payload.get("data") or {}),
                )
            except (PermissionError, ValueError, TypeError) as exc:
                self.bridge.send(MessageType.ERROR.value, {"reason": str(exc)})
                return
            payload = {"op_seq": op.seq, "op_id": op.data.get("op_id"),
                       "player_id": op.player_id, "op": op.op, "data": op.data}
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
        elif env.type == MessageType.TRAVEL_ABORT.value:
            txn = self.host.session.travel.current
            if txn and int(env.payload.get('epoch', -1)) == txn.epoch:
                self.host.session.travel.abort(txn.txn_id)
                await self.host.broadcast(MessageType.TRAVEL_ABORT, {
                    'txn_id': txn.txn_id, 'epoch': txn.epoch,
                    'reason': env.payload.get('reason', 'participant_abort')})
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
            "epoch": txn.epoch,
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
            commit = self.host.session.travel.mark_ready(player_id, txn_id, int(payload.get('epoch', -1)))
        except KeyError:
            return
        if commit:
            txn = self.host.session.travel.current
            assert txn is not None
            out = {"txn_id": txn.txn_id, "epoch": txn.epoch, "zone_id": txn.zone_id, "actor_ids": txn.actor_ids}
            await self.host.broadcast(MessageType.TRAVEL_VIEW_BATCH, {'txn_id': txn.txn_id, 'epoch': txn.epoch, 'kind': 'begin'})
            self.bridge.send(MessageType.TRAVEL_VIEW_BATCH.value, {'txn_id': txn.txn_id, 'epoch': txn.epoch, 'kind': 'begin'})
            self.bridge.send(MessageType.TRAVEL_COMMIT.value, out)
            await self.host.broadcast(MessageType.TRAVEL_COMMIT, out)
            self.host.session.travel.begin_zone_wait(txn_id)

    async def _travel_zone_ready(self, player_id: str, payload: dict) -> None:
        txn_id = str(payload.get("txn_id") or "")
        if not txn_id:
            return
        try:
            expected = self.host.session.travel.current
            if expected is None or int(payload.get('epoch', -1)) != expected.epoch:
                return
            resume = self.host.session.travel.mark_zone_ready(player_id, txn_id, int(payload.get('epoch', -1)))
        except KeyError:
            return
        if resume:
            out = {"txn_id": txn_id, "epoch": expected.epoch}
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
        self.view_updates_received = 0
        self.last_view_update_size = 0
        self.last_view_update_msg_id = None
        self._last_game_sequence = 0
        self.travel_epoch = 0
        self.buffer_view_updates = False
        self.buffered_view_updates = []
        self.max_buffered_view_updates = 256
        self.travel_batch_complete = False
        self.local_zone_loaded = False
        self.save_receiver: SaveReceiver | None = None

    async def start(self) -> Envelope:
        self.loop = asyncio.get_running_loop()
        self.bridge.start()
        return await self.client.connect()

    async def stop(self) -> None:
        self.bridge.close()
        await self.client.close()

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
            # A connected script bridge is necessary but does not claim that
            # client simulation suppression has been safely installed.
            await self.client.send(MessageType.READINESS, {"bridge_connected": True,
                "simulation_authority_ready": False})
            return
        if event.type == "simulation.authority":
            status = dict(event.payload or {})
            authority_ready = bool(status.get("role") == "client" and status.get("installed"))
            await self.client.send(MessageType.READINESS, {"bridge_connected": True,
                "simulation_authority_ready": authority_ready})
            return
        # A client only sends local input upstream; it never echoes host game messages.
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
        if env.type == MessageType.SAVE_MANIFEST.value:
            try:
                self.save_receiver = SaveReceiver(env.payload, resolve_sims_user_dir() / "saves")
                await self.client.send(MessageType.READINESS, {"save_total_bytes": self.save_receiver.total, "save_bytes_received": 0})
            except ValueError as exc:
                await self.client.send(MessageType.SAVE_ERROR, {"reason": str(exc)})
            return
        if env.type == MessageType.SAVE_CHUNK.value:
            try:
                if not self.save_receiver: raise ValueError("save_transfer_not_started")
                raw = base64.b64decode(str(env.payload.get("payload_b64", "")), validate=True)
                self.save_receiver.write_chunk(int(env.payload.get("index", -1)), raw)
                await self.client.send(MessageType.READINESS, {"save_total_bytes": self.save_receiver.total, "save_bytes_received": self.save_receiver.received})
            except Exception as exc:
                if self.save_receiver: self.save_receiver.abort()
                await self.client.send(MessageType.SAVE_ERROR, {"reason": str(exc)})
            return
        if env.type == MessageType.SAVE_END.value:
            try:
                if not self.save_receiver: raise ValueError("save_transfer_not_started")
                final_path, backup = self.save_receiver.finalize(str(env.payload.get("session_id") or "unknown"))
                await self.client.send(MessageType.SAVE_ACK, {"slot_id": self.save_receiver.slot_id, "sha256": self.save_receiver.expected_hash,
                    "total_bytes": self.save_receiver.total, "path": str(final_path), "backup_created": bool(backup)})
            except Exception as exc:
                await self.client.send(MessageType.SAVE_ERROR, {"reason": str(exc)})
            finally:
                self.save_receiver = None
            return
        if env.type == MessageType.GAME_RAW_MESSAGE.value:
            sequence = int(env.payload.get('sequence', 0))
            if sequence <= self._last_game_sequence:
                return
            try:
                raw = base64.b64decode(str(env.payload['payload_b64']), validate=True)
                if len(raw) > MAX_GAME_MESSAGE_BYTES:
                    raise ValueError('payload_too_large')
            except Exception as exc:
                self.bridge.send('sidecar.error', {'reason': 'raw_message_invalid:%s' % exc})
                return
            epoch = env.payload.get('epoch')
            if self.travel_epoch and epoch is None:
                self.bridge.send('sidecar.error', {'reason': 'missing_travel_epoch'})
                return
            if epoch is not None and self.travel_epoch and int(epoch) != self.travel_epoch:
                self.bridge.send('sidecar.error', {'reason': 'stale_travel_epoch'})
                return
            self._last_game_sequence = sequence
            if self.buffer_view_updates:
                if len(self.buffered_view_updates) >= self.max_buffered_view_updates:
                    self.bridge.send('sidecar.error', {'reason': 'view_update_buffer_full'})
                    return
                self.buffered_view_updates.append(dict(env.payload))
                self.buffered_view_updates.sort(key=lambda item: int(item.get('sequence', 0)))
            else:
                self._apply_game_message(env.payload)
            return
        if env.type == MessageType.TRAVEL_VIEW_BATCH.value:
            epoch = int(env.payload.get('epoch', -1))
            if epoch != self.travel_epoch:
                self.bridge.send('sidecar.error', {'reason': 'stale_travel_epoch'})
                return
            kind = str(env.payload.get('kind', '')).lower()
            if kind == 'begin':
                self.buffer_view_updates = True
                self.travel_batch_complete = False
                self.buffered_view_updates = []
            elif kind == 'end':
                self.travel_batch_complete = True
                if self.local_zone_loaded:
                    self._finish_local_travel(env.payload)
            self.bridge.send(env.type, env.payload)
            return
        if env.type == MessageType.TRAVEL_PROPOSE.value:
            incoming = int(env.payload.get('epoch', 0))
            if incoming <= self.travel_epoch:
                return
            self.travel_epoch = incoming
            self.local_zone_loaded = False
            self.travel_batch_complete = False
            self.buffer_view_updates = True
            self.buffered_view_updates = []
            self.bridge.send("travel.prepare", env.payload)
        elif env.type == MessageType.TRAVEL_RESUME.value:
            if int(env.payload.get('epoch', -1)) == self.travel_epoch and self.travel_batch_complete:
                for payload in sorted(self.buffered_view_updates, key=lambda item: int(item.get('sequence', 0))):
                    self._apply_game_message(payload)
                self.buffered_view_updates = []
                self.buffer_view_updates = False
            self.bridge.send(env.type, env.payload)
        elif env.type in (MessageType.TRAVEL_COMMIT.value, MessageType.TRAVEL_ABORT.value):
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

    def _apply_game_message(self, payload: dict) -> None:
        self.view_updates_received += 1
        self.last_view_update_size = len(base64.b64decode(str(payload['payload_b64'])))
        self.last_view_update_msg_id = int(payload['msg_id'])
        self.bridge.send(MessageType.GAME_RAW_MESSAGE.value, payload)

    def mark_local_zone_loaded(self, epoch: int) -> bool:
        if int(epoch) != self.travel_epoch:
            return False
        self.local_zone_loaded = True
        return self.travel_batch_complete and self._finish_local_travel({'epoch': epoch})

    def _finish_local_travel(self, payload: dict) -> bool:
        for item in sorted(self.buffered_view_updates, key=lambda item: int(item.get('sequence', 0))):
            self._apply_game_message(item)
        self.buffered_view_updates = []
        self.buffer_view_updates = False
        return True
