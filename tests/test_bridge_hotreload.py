import importlib.util
import socket
import threading
import time
from pathlib import Path


def _load_bridge_client():
    path = Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod' / 'bridge_client.py'
    spec = importlib.util.spec_from_file_location('kermp_sims_bridge_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_idle_server(port):
    """Accept one connection, read the client hello, then hold the link open
    (blocked on recv) until the client disconnects so the reader stays blocked
    in ``readline()``."""
    got_hello = threading.Event()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)

    def run():
        try:
            conn, _ = server.accept()
            conn.settimeout(2.0)
            try:
                if conn.recv(4096):
                    got_hello.set()
                conn.settimeout(30.0)
                while conn.recv(4096):
                    pass
            except OSError:
                pass
            finally:
                conn.close()
        finally:
            server.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return got_hello


def test_stop_unblocks_a_reader_blocked_in_readline():
    module = _load_bridge_client()
    port = _free_port()
    module.PORT = port
    got_hello = _start_idle_server(port)

    client = module.KerMPBridgeClient()
    client.start()
    assert got_hello.wait(2.0)
    time.sleep(0.05)  # let the reader settle into readline()
    thread = client._thread
    assert thread is not None

    client.stop()
    assert not thread.is_alive()

    # repeated stop is a no-op and must not raise
    client.stop()


def test_second_bridge_instance_reconnects_after_stop():
    module = _load_bridge_client()

    port1 = _free_port()
    module.PORT = port1
    got_hello1 = _start_idle_server(port1)
    client1 = module.KerMPBridgeClient()
    client1.start()
    assert got_hello1.wait(2.0)
    thread1 = client1._thread
    client1.stop()
    assert not thread1.is_alive()

    port2 = _free_port()
    module.PORT = port2
    got_hello2 = _start_idle_server(port2)
    client2 = module.KerMPBridgeClient()
    client2.start()
    assert got_hello2.wait(2.0)
    client2.stop()


def test_repeated_reload_like_lifecycle_leaks_no_active_readers():
    module = _load_bridge_client()
    for _ in range(3):
        port = _free_port()
        module.PORT = port
        got_hello = _start_idle_server(port)
        client = module.KerMPBridgeClient()
        client.start()
        assert got_hello.wait(2.0)
        thread = client._thread
        client.stop()
        assert not thread.is_alive()
