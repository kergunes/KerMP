import importlib.util
from pathlib import Path


_path = Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod' / 'build_adapter.py'
_spec = importlib.util.spec_from_file_location('kermp_sims_build_adapter', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
SimsBuildAdapter = _module.SimsBuildAdapter
normalize_operation = _module.normalize_operation


def test_normalize_captured_wall_operation():
    assert normalize_operation({
        "op": "wall.create", "data": {"x1": 1, "x2": 4}, "op_seq": 7
    }) == {
        "op": "wall.create", "data": {"x1": 1, "x2": 4}, "op_id": "7"
    }


def test_remote_apply_is_idempotent_and_suppressed_from_capture():
    applied = []
    adapter = SimsBuildAdapter()
    adapter.configure(apply=lambda operation: applied.append(operation))
    payload = {"op": "wall.create", "op_seq": 11, "data": {"level": 0}}

    assert adapter.apply_remote(payload)
    assert not adapter.apply_remote(payload)
    assert len(applied) == 1
    assert adapter.capture_local(payload) is not False


def test_capture_is_rejected_during_remote_apply():
    adapter = SimsBuildAdapter()
    observed = []

    def apply(operation):
        observed.append(adapter.capture_local(operation))

    adapter.configure(apply=apply)
    assert adapter.apply_remote({"op": "wall.create", "op_seq": 1, "data": {}})
    assert observed == [False]
