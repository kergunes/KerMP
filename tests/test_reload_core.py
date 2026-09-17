import importlib.util
from pathlib import Path
from types import ModuleType


def _load_reload_core():
    path = Path(__file__).resolve().parents[1] / "sims_mod_src" / "kermp_mod" / "reload_core.py"
    spec = importlib.util.spec_from_file_location("kermp_reload_core_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_exec_module_code_rolls_back_on_exception():
    core = _load_reload_core()
    module = ModuleType("example")
    module.VALUE = 1
    try:
        core.exec_module_code(module, compile("VALUE = 2\nraise RuntimeError('boom')", "x.py", "exec"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected exception")
    assert module.VALUE == 1


def test_exec_module_code_updates_existing_namespace_identity():
    core = _load_reload_core()
    module = ModuleType("example")
    namespace_id = id(module.__dict__)
    core.exec_module_code(module, compile("VALUE = 7", "x.py", "exec"))
    assert id(module.__dict__) == namespace_id
    assert module.VALUE == 7


def test_wrapper_depth_detects_stacking():
    core = _load_reload_core()

    def base():
        pass

    def first():
        pass

    first._kermp_wrapped = True
    first._kermp_original = base

    def second():
        pass

    second._kermp_wrapped = True
    second._kermp_original = first
    assert core.wrapper_depth(second) == 2
