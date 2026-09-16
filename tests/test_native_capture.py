import hashlib
import importlib.util
from pathlib import Path


path = Path(__file__).parents[1] / 'kermp' / 'native_capture.py'
spec = importlib.util.spec_from_file_location('kermp_native_capture', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CaptureQueue = module.CaptureQueue
serialize_metadata = module.serialize_metadata


def test_bounded_queue_evicts_oldest_and_reports_drops():
    queue = CaptureQueue(2)
    queue.push(b'a', 11)
    queue.push(b'b', 12)
    queue.push(b'c', 13)
    assert queue.dropped_count == 1
    assert queue.take()['payload_hash'] == hashlib.sha256(b'b').hexdigest()
    assert queue.take()['payload_size'] == 1
    assert queue.take() is None


def test_metadata_serialization_is_deterministic_and_safe():
    assert serialize_metadata({'payload_size': 4, 'capture_id': 1}) == '{"capture_id":1,"payload_size":4}'
