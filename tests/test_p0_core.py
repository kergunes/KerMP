import base64
from pathlib import Path

import pytest

from kermp.compatibility import CompatibilityManifest, compare_manifests
from kermp.routing import DialogRouter, NetworkedCommandRouter, PlayerAudience, AudienceKind
from kermp.save_sync import SaveReceiver, SaveSlot, discover_slots
from kermp.session import HostSession


def manifest(player_id="p", **updates):
    data = dict(player_id=player_id, display_name=player_id, game_version="1.2.3",
                enabled_packs=("EP01",), native_available=True, native_game_build_supported=True)
    data.update(updates)
    return CompatibilityManifest(**data)


def test_strict_manifest_comparison_reasons():
    host = manifest("host")
    assert compare_manifests(host, manifest("client"), native_required=True) == []
    assert "game_version_mismatch" in compare_manifests(host, manifest("client", game_version="1.2.4"), native_required=False)
    assert "pack_mismatch" in compare_manifests(host, manifest("client", enabled_packs=("EP02",)), native_required=False)
    assert "native_api_mismatch" in compare_manifests(host, manifest("client", native_available=False), native_required=True)


def test_save_receiver_hash_backup_and_sequence_guard(tmp_path):
    saves = tmp_path / "saves"; saves.mkdir()
    previous = saves / "Slot_00000001.save"; previous.write_bytes(b"old")
    source = tmp_path / "Slot_00000001.save"; source.write_bytes(b"new canonical save")
    slot = SaveSlot.from_path(source)
    receiver = SaveReceiver(slot.manifest("session", chunk_size=8), saves)
    payload = source.read_bytes()
    with pytest.raises(ValueError, match="out_of_order"):
        receiver.write_chunk(1, payload[:8])
    for index in range(0, len(payload), 8):
        receiver.write_chunk(index // 8, payload[index:index + 8])
    final, backup = receiver.finalize("session")
    assert final.read_bytes() == payload
    assert backup and backup.read_bytes() == b"old"
    assert discover_slots(tmp_path)[0].slot_id == "Slot_00000001"


def test_router_whitelist_dialog_ownership_and_audience():
    router = NetworkedCommandRouter()
    router.register("interaction.choice", lambda player, payload: {"player": player, "choice": payload["choice"]})
    assert router.dispatch("p2", "interaction.choice", {"choice": "eat"}, "r1")["result"]["choice"] == "eat"
    with pytest.raises(ValueError, match="not_allowed"):
        router.dispatch("p2", "python.exec", {}, "r2")
    dialogs = DialogRouter(); opened = dialogs.open("p2", "yes_no", {"text": "Continue?"})
    with pytest.raises(ValueError, match="not_owned"):
        dialogs.respond("host", opened["dialog_id"], {})
    assert dialogs.respond("p2", opened["dialog_id"], {"accepted": True})["response"]["accepted"]
    assert PlayerAudience(AudienceKind.ALL_EXCEPT, "p2").recipients({"host", "p2", "p3"}, "host") == {"host", "p3"}


def test_readiness_is_not_tcp_connected_and_clock_is_host_state():
    host_manifest = manifest("host")
    session = HostSession(host_manifest=host_manifest)
    session.add_player("host", "Host", host_manifest)
    session.add_player("p2", "Player2", manifest("p2"))
    assert not session.core_readiness().core_ready
    for player_id in ("host", "p2"):
        session.update_readiness(player_id, save_hash="hash", bridge_connected=True, zone_ready=True,
                                 simulation_authority_ready=True, native_ready=True)
    assert session.core_readiness().core_ready
    session.clock.update(sequence=1, speed=3, paused=False)
    assert session.snapshot()["clock"] == {"sequence": 1, "game_time": None, "speed": 3, "paused": False}
