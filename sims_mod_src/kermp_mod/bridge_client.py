"""Source-only Sims-side bridge prototype.

Important: The Sims 4 currently uses its own embedded Python runtime. This file is
kept as source until compiled with a matching Sims Python toolchain into .pyc and
packed as .ts4script. Do not drop this .py directly into Mods expecting it to run.
"""

import json
import socket
import threading
import time

HOST = "127.0.0.1"
PORT = 17654


class KerMPBridgeClient(object):
    def __init__(self):
        self.sock = None
        self._stop = False
        self.handlers = {}

    def connect(self):
        while not self._stop:
            try:
                self.sock = socket.create_connection((HOST, PORT), timeout=2.0)
                self.sock.settimeout(None)
                self._send("game.hello", {"bridge_version": 1})
                self._read_loop()
            except Exception:
                time.sleep(1.0)
            finally:
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None

    def start(self):
        t = threading.Thread(target=self.connect)
        t.daemon = True
        t.start()

    def stop(self):
        """Signal the reader thread to exit and close the socket."""
        self._stop = True
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def on(self, event_type, handler):
        self.handlers[event_type] = handler

    def emit(self, event_type, payload):
        return self._send(event_type, payload)

    def _send(self, event_type, payload):
        if not self.sock:
            return False
        raw = json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")) + "\n"
        self.sock.sendall(raw.encode("utf-8"))
        return True

    def _read_loop(self):
        f = self.sock.makefile("rb")
        while not self._stop:
            line = f.readline()
            if not line:
                break
            msg = json.loads(line.decode("utf-8"))
            handler = self.handlers.get(msg.get("type"))
            if handler:
                handler(msg.get("payload", {}))
