"""Source-only Sims-side bridge prototype.

The bridge is process-stable during KerMP hot reloads. Its dispatch gate can
quiesce an in-flight handler, queue new network messages, and then drain them in
order against the new handler generation.
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
        self._dispatch_condition = threading.Condition(self._lock)
        self._send_lock = threading.Lock()
        self._thread = None
        self._start_count = 0
        self._dispatch_paused = False
        self._active_dispatches = 0
        self._queued_messages = []
        self._max_queued_messages = 256
        self._dropped_messages = 0
        self._dispatch_error_count = 0
        self._last_dispatch_error = None

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

    def pause_dispatch(self, timeout=5.0):
        """Pause new dispatches and wait until every old handler has returned."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._dispatch_condition:
            self._dispatch_paused = True
            while self._active_dispatches:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._dispatch_condition.wait(remaining)
            return True

    def _run_handler(self, handler, payload):
        try:
            handler(payload)
            return None
        except Exception as exc:
            with self._lock:
                self._dispatch_error_count += 1
                self._last_dispatch_error = '%s: %s' % (type(exc).__name__, exc)
            return exc
        finally:
            with self._dispatch_condition:
                self._active_dispatches -= 1
                self._dispatch_condition.notify_all()

    def resume_dispatch(self):
        """Drain queued messages FIFO while the gate remains closed.

        New arrivals continue to queue until the backlog is empty, preventing a
        newer packet from overtaking one captured during reload.
        """
        errors = 0
        while True:
            with self._dispatch_condition:
                if not self._queued_messages:
                    self._dispatch_paused = False
                    self._dispatch_condition.notify_all()
                    return errors
                msg = self._queued_messages.pop(0)
                handler = self.handlers.get(msg.get("type"))
                if not handler:
                    continue
                self._active_dispatches += 1
            error = self._run_handler(handler, msg.get("payload", {}))
            if error is not None:
                errors += 1

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
        with self._dispatch_condition:
            if self._dispatch_paused:
                if len(self._queued_messages) >= self._max_queued_messages:
                    self._queued_messages.pop(0)
                    self._dropped_messages += 1
                self._queued_messages.append(msg)
                return
            handler = self.handlers.get(msg.get("type"))
            if not handler:
                return
            self._active_dispatches += 1
        self._run_handler(handler, msg.get("payload", {}))

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
                "active_dispatches": self._active_dispatches,
                "queued_messages": len(self._queued_messages),
                "dropped_messages": self._dropped_messages,
                "dispatch_error_count": self._dispatch_error_count,
                "last_dispatch_error": self._last_dispatch_error,
                "connected": bool(self.sock),
                "handler_count": len(self.handlers),
            }
