"""Source-only Sims-side bridge prototype.

The bridge is process-stable during KerMP hot reloads.  Its dispatch gate keeps
network messages away from a module while that module namespace is being
re-executed.
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
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._thread = None
        self._start_count = 0
        self._dispatch_paused = False
        self._queued_messages = []
        self._max_queued_messages = 256
        self._dropped_messages = 0

    def connect(self):
        while not self._stop:
            current = None
            try:
                current = socket.create_connection((HOST, PORT), timeout=2.0)
                current.settimeout(None)
                with self._lock:
                    if self._stop:
                        current.close()
                        return
                    self.sock = current
                self._send("game.hello", {"bridge_version": 1})
                self._read_loop(current)
            except Exception:
                if not self._stop:
                    time.sleep(1.0)
            finally:
                try:
                    if current:
                        current.close()
                except Exception:
                    pass
                with self._lock:
                    if self.sock is current:
                        self.sock = None

    def start(self):
        """Start exactly one reconnect thread for this bridge instance."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop = False
            thread = threading.Thread(target=self.connect)
            thread.daemon = True
            self._thread = thread
            self._start_count += 1
            thread.start()
            return True

    def stop(self):
        with self._lock:
            self._stop = True
            current = self.sock
            self.sock = None
        try:
            if current:
                current.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            if current:
                current.close()
        except Exception:
            pass

    def on(self, event_type, handler):
        with self._lock:
            self.handlers[event_type] = handler

    def emit(self, event_type, payload):
        return self._send(event_type, payload)

    def pause_dispatch(self):
        with self._lock:
            self._dispatch_paused = True

    def resume_dispatch(self):
        with self._lock:
            self._dispatch_paused = False
            queued = list(self._queued_messages)
            self._queued_messages = []
        for msg in queued:
            self._dispatch_message(msg)

    def _send(self, event_type, payload):
        with self._lock:
            current = self.sock
        if not current:
            return False
        raw = json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")) + "\n"
        try:
            with self._send_lock:
                current.sendall(raw.encode("utf-8"))
            return True
        except Exception:
            return False

    def _dispatch_message(self, msg):
        with self._lock:
            if self._dispatch_paused:
                if len(self._queued_messages) >= self._max_queued_messages:
                    self._queued_messages.pop(0)
                    self._dropped_messages += 1
                self._queued_messages.append(msg)
                return
            handler = self.handlers.get(msg.get("type"))
        if handler:
            handler(msg.get("payload", {}))

    def _read_loop(self, current):
        handle = current.makefile("rb")
        try:
            while not self._stop:
                line = handle.readline()
                if not line:
                    break
                msg = json.loads(line.decode("utf-8"))
                self._dispatch_message(msg)
        finally:
            try:
                handle.close()
            except Exception:
                pass

    def status(self):
        with self._lock:
            return {
                "thread_alive": bool(self._thread is not None and self._thread.is_alive()),
                "start_count": self._start_count,
                "dispatch_paused": self._dispatch_paused,
                "queued_messages": len(self._queued_messages),
                "dropped_messages": self._dropped_messages,
                "connected": bool(self.sock),
                "handler_count": len(self.handlers),
            }
