import importlib.util
import sys
import types
from pathlib import Path


TRACE_PATH = (Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod' /
              'interaction_trace.py')


def load_trace():
    spec = importlib.util.spec_from_file_location('test_interaction_trace_module', TRACE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trace_wrapper_is_passive_and_records_bounded_values():
    trace = load_trace()
    trace._enabled = True
    trace._started_at = trace.time.monotonic()

    def add(left, right=0):
        return left + right

    wrapped = trace._make_wrapper('example.math.add', add)
    assert wrapped(2, right=3) == 5
    values = trace.events()
    assert [value['phase'] for value in values] == ['enter', 'exit']
    assert values[0]['function'] == 'add'
    assert values[1]['return'] == 5
    assert values[1]['return_type'] == 'builtins.int'


def test_trace_patch_restores_exact_original():
    trace = load_trace()
    module_name = 'kermp_trace_fake_module'
    module = types.ModuleType(module_name)

    class Target:
        def call(self, value):
            return value

    original = Target.call
    module.Target = Target
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        trace._patch(module_name, 'Target', 'call')
        assert Target().call('ok') == 'ok'
        trace.stop()
        assert Target.call is original
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def test_primitive_summary_never_recursively_reprs_game_objects():
    trace = load_trace()

    class GameObject:
        id = 2**63 + 123
        revision = 9

        def __repr__(self):
            raise AssertionError('repr must not be called')

    summary = trace._primitive(GameObject())
    assert summary['id'] == 2**63 + 123
    assert summary['revision'] == 9
    assert summary['type'].endswith('.GameObject')
