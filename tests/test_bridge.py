import json
import socket
import threading
import time

from kermp.bridge import LocalGameBridge


def test_sims_bridge_emit_returns_send_result():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod' / 'bridge_client.py'
    spec = importlib.util.spec_from_file_location('kermp_sims_bridge_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module.KerMPBridgeClient()
    client._send = lambda event_type, payload: 'sent-result'
    assert client.emit('test.event', {}) == 'sent-result'


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_local_game_bridge_survives_idle_connection():
    port = _free_port()
    received = []
    arrived = threading.Event()
    bridge = LocalGameBridge(port, lambda event: (received.append(event), arrived.set()))
    bridge.start()

    deadline = time.time() + 2
    sock = None
    while time.time() < deadline:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            break
        except OSError:
            time.sleep(0.02)
    assert sock is not None

    try:
        # Regression: the old bridge closed an otherwise healthy connection after
        # 0.5 s of inactivity, so commands typed later never reached the sidecar.
        time.sleep(0.75)
        wire = json.dumps({"type": "game.ping", "payload": {"after_idle": True}}) + "\n"
        sock.sendall(wire.encode("utf-8"))
        assert arrived.wait(1.0)
        assert received[0].type == "game.ping"
        assert received[0].payload == {"after_idle": True}
    finally:
        sock.close()
        bridge.close()
