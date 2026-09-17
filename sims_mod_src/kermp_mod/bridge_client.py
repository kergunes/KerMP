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
        self._file = None
        self._stop = False
        self.handlers = {}
        self._thread = None

    def connect(self):
        while not self._stop:
            try:
                self.sock = socket.create_connection((HOST, PORT), timeout=2.0)
                self.sock.settimeout(None)
                self._send("game.hello", {"bridge_version": 1})
                self._read_loop()
            except Exception:
                if self._stop:
                    break
                time.sleep(1.0)
            finally:
                self._close_transport()

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self.connect)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        """Idempotently stop the reader thread and release the socket.

        Closing a socket does not reliably unblock a thread blocked in
        ``makefile().readline()`` because the file object holds a reference to
        the descriptor. Shutdown the socket first to force the pending read to
        return, then close the file object and the socket, and join the reader
        thread with a short bound so it cannot outlive this instance.
        """
        self._stop = True
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        self._close_transport()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _close_transport(self):
        f, self._file = self._file, None
        try:
            if f is not None:
                f.close()
        except Exception:
            pass
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
        self._file = f
        try:
            while not self._stop:
                line = f.readline()
                if not line:
                    break
                msg = json.loads(line.decode("utf-8"))
                handler = self.handlers.get(msg.get("type"))
                if handler:
                    handler(msg.get("payload", {}))
        finally:
            self._file = None
            try:
                f.close()
            except Exception:
                pass
