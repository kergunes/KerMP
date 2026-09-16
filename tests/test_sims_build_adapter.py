import importlib.util
from pathlib import Path


_path = Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod' / 'build_adapter.py'
_spec = importlib.util.spec_from_file_location('kermp_sims_build_adapter', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
SimsBuildAdapter = _module.SimsBuildAdapter
normalize_operation = _module.normalize_operation
contour_delta = _module.contour_delta


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


def test_contour_delta_reports_added_and_removed_normalized_values():
    before = [{'level': 0, 'start': [1, 1], 'end': [2, 1]}]
    after = before + [{'level': 0, 'start': [2, 1], 'end': [3, 1]}]
    delta = contour_delta(before, after)
    assert delta['before_count'] == 1
    assert delta['after_count'] == 2
    assert delta['added'] == [after[1]]
    assert delta['removed'] == []


def test_contour_delta_reports_changed_stable_identity():
    before = [{'wall_id': 9, 'level': 0, 'end': [2, 1]}]
    after = [{'wall_id': 9, 'level': 1, 'end': [2, 1]}]
    delta = contour_delta(before, after)
    assert delta['changed'][0]['identity'] == '9'
