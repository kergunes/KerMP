import base64
import hashlib
import json
import socket
import tempfile
from argparse import Namespace
from pathlib import Path

from kermp.protocol import Envelope, MessageType
from kermp.save_sync import SaveSlot
from tools.fake_player import FakePlayer, FakePlayerState, move_payload, parse_command


def make_client(accept_save_sync=False):
    left, right = socket.socketpair()
    args = Namespace(player_id="player2", name="Player2", accept_save_sync=accept_save_sync, verbose=False, auto_travel_ack=False)
    return FakePlayer(left, args), right


def read_sent(sock):
    return Envelope.from_line(sock.recv(65536))


def test_command_parser_and_move_payload():
    assert parse_command("  travel 42 ") == ("travel", ["42"])
    assert parse_command("") == ("", [])
    assert move_payload("7", ["1", "2", "3", "0", "0", "0", "1"]) == {
        "object_id": "7", "transform": {"translation": [1.0, 2.0, 3.0], "orientation": [0.0, 0.0, 0.0, 1.0]}}


def test_travel_clock_dialog_and_build_payloads():
    client, peer = make_client()
    try:
        client.run_command("travel 42")
        env = read_sent(peer)
        assert env.type == MessageType.TRAVEL_REQUEST.value
        assert env.payload == {"zone_id": "42", "actor_ids": []}
        client.run_command("speed 3")
        assert read_sent(peer).payload == {"speed": 3}
        client.handle(Envelope.make(MessageType.DIALOG_OPEN, {"dialog_id": "d1", "dialog_type": "picker"}, "host"))
        client.run_command("dialog d1 select blue")
        assert read_sent(peer).payload == {"dialog_id": "d1", "response": {"value": "blue"}}
        client.state.build_lock_owner = "other"
        client.run_command("move 7 1 2 3 0 0 0 1")
        assert client.pending_build and client.pending_build[0][0] == "object.move"
        assert read_sent(peer).type == MessageType.BUILD_LOCK_REQUEST.value
    finally:
        client.sock.close(); peer.close()


def test_state_updates_raw_and_travel():
    client, peer = make_client()
    try:
        client.handle(Envelope.make(MessageType.GAME_RAW_MESSAGE, {"msg_id": 9, "sequence": 2, "payload_b64": base64.b64encode(b"abc").decode()}, "host"))
        assert (client.state.raw_messages_received, client.state.last_msg_id, client.state.last_size) == (1, 9, 3)
        client.handle(Envelope.make(MessageType.TRAVEL_PROPOSE, {"txn_id": "t", "epoch": 4, "zone_id": "42"}, "host"))
        client.handle(Envelope.make(MessageType.TRAVEL_VIEW_BATCH, {"txn_id": "t", "epoch": 4, "kind": "begin"}, "host"))
        assert client.state.travel["phase"] == "view_batch_begin" and client.state.travel["batch_open"]
    finally:
        client.sock.close(); peer.close()


def test_accept_save_sync_validates_hash_in_temp_storage():
    client, peer = make_client(True)
    try:
        data = b"fake save payload"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Slot_ABC.save"
            path.write_bytes(data)
            slot = SaveSlot.from_path(path)
            manifest = slot.manifest("session", chunk_size=len(data))
        client.handle(Envelope.make(MessageType.SAVE_MANIFEST, manifest, "host"))
        client.handle(Envelope.make(MessageType.SAVE_CHUNK, {"index": 0, "payload_b64": base64.b64encode(data).decode()}, "host"))
        client.handle(Envelope.make(MessageType.SAVE_END, {"session_id": "session", "sha256": hashlib.sha256(data).hexdigest()}, "host"))
        assert read_sent(peer).type == MessageType.SAVE_ACK.value
        assert not list(Path(client.save_tmp).glob("*.partial"))
    finally:
        client.sock.close(); peer.close()


def test_build_lock_state_flushes_queue():
    client, peer = make_client()
    try:
        client.state.build_lock_owner = "other"
        client.run_command("scale 7 1.5")
        peer.recv(65536)
        client.handle(Envelope.make(MessageType.BUILD_LOCK_STATE, {"owner_id": "player2", "granted_to": "player2"}, "host"))
        assert read_sent(peer).payload == {"op": "object.scale", "data": {"object_id": "7", "scale": 1.5}}
    finally:
        client.sock.close(); peer.close()
