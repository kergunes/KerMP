import importlib.util
import threading
from pathlib import Path


def _load_bridge_module():
    path = Path(__file__).resolve().parents[1] / "sims_mod_src" / "kermp_mod" / "bridge_client.py"
    spec = importlib.util.spec_from_file_location("kermp_bridge_client_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_paused_dispatch_uses_latest_handler_on_resume():
    module = _load_bridge_module()
    client = module.KerMPBridgeClient()
    calls = []

    client.on("event", lambda payload: calls.append(("old", payload["value"])))
    client.pause_dispatch()
    client._dispatch_message({"type": "event", "payload": {"value": 7}})
    assert calls == []
    assert client.status()["queued_messages"] == 1

    client.on("event", lambda payload: calls.append(("new", payload["value"])))
    client.resume_dispatch()

    assert calls == [("new", 7)]
    assert client.status()["queued_messages"] == 0
    assert client.status()["dispatch_paused"] is False


def test_start_is_idempotent_while_thread_is_alive():
    module = _load_bridge_module()
    client = module.KerMPBridgeClient()
    release = threading.Event()
    entered = threading.Event()

    def fake_connect():
        entered.set()
        release.wait(2)

    client.connect = fake_connect
    assert client.start() is True
    assert entered.wait(1)
    assert client.start() is False
    assert client.status()["start_count"] == 1
    release.set()
    client._thread.join(2)
