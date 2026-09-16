"""Pure-Python model for the bounded native capture contract."""
from collections import deque
import hashlib
import json


class CaptureQueue(object):
    def __init__(self, capacity=64):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._items = deque(maxlen=capacity)
        self.capacity = capacity
        self.dropped_count = 0
        self._next_id = 1

    def push(self, payload, thread_id=0):
        raw = bytes(payload)
        if len(self._items) == self.capacity:
            self.dropped_count += 1
        item = {"capture_id": self._next_id, "thread_id": int(thread_id),
                "payload_size": len(raw),
                "payload_hash": hashlib.sha256(raw).hexdigest()}
        self._next_id += 1
        self._items.append(item)
        return item

    def take(self):
        return self._items.popleft() if self._items else None


def serialize_metadata(metadata):
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))
