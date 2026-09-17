import importlib.util
import sys
import types
from pathlib import Path

_MODULE = Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod' / 'lifecycle_guard.py'


def _load_guard():
    spec = importlib.util.spec_from_file_location('kermp_lifecycle_guard_test', _MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Modules:
    def __init__(self):
        self.saved = {}

    def put(self, name, module):
        self.saved[name] = sys.modules.get(name)
        sys.modules[name] = module

    def close(self):
        for name, previous in self.saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _runtime(*, traveling=False, zone=True, client=True, active_sim=True):
    modules = Modules()

    game_services = types.ModuleType('game_services')
    game_services.service_manager = types.SimpleNamespace(is_traveling=traveling)
    modules.put('game_services', game_services)

    services = types.ModuleType('services')
    zone_obj = object() if zone else None
    client_obj = types.SimpleNamespace(
        active_sim_info=types.SimpleNamespace(id=77) if active_sim else None
    ) if client else None
    services.current_zone = lambda: zone_obj
    services.get_first_client = lambda: client_obj
    modules.put('services', services)

    scheduling = types.ModuleType('scheduling')
    calls = []

    class Timeline:
        def simulate(self):
            calls.append('simulate')
            return 'simulated'

    scheduling.Timeline = Timeline
    modules.put('scheduling', scheduling)
    return modules, scheduling, calls


def _hooks():
    status = {'bypass': False}
    patches = []
    return types.SimpleNamespace(
        _sidecar_role='client',
        _simulation_bypass=False,
        _timeline_suppression_installed=False,
        _simulation_status=status,
        _install_timeline_suppression=lambda: False,
        _record_patch=lambda target, attr, original: patches.append((target, attr, original)),
        _log=lambda message: None,
        patches=patches,
    )


def test_normal_client_gameplay_is_suppressed():
    modules, scheduling, calls = _runtime()
    try:
        guard = _load_guard()
        hooks = _hooks()
        assert guard.install(hooks) is True
        assert hooks._install_timeline_suppression() is True
        assert scheduling.Timeline().simulate() is None
        assert calls == []
        assert hooks._simulation_status['lifecycle_bypass'] is False
    finally:
        modules.close()


def test_game_traveling_runs_original_timeline():
    modules, scheduling, calls = _runtime(traveling=True)
    try:
        guard = _load_guard()
        hooks = _hooks()
        guard.install(hooks)
        hooks._install_timeline_suppression()
        assert scheduling.Timeline().simulate() == 'simulated'
        assert calls == ['simulate']
        assert hooks._simulation_status['lifecycle_bypass'] is True
    finally:
        modules.close()


def test_initial_load_without_active_sim_runs_original_timeline():
    modules, scheduling, calls = _runtime(active_sim=False)
    try:
        guard = _load_guard()
        hooks = _hooks()
        guard.install(hooks)
        hooks._install_timeline_suppression()
        assert scheduling.Timeline().simulate() == 'simulated'
        assert calls == ['simulate']
    finally:
        modules.close()


def test_shutdown_without_live_zone_runs_original_timeline():
    modules, scheduling, calls = _runtime(zone=False)
    try:
        guard = _load_guard()
        hooks = _hooks()
        guard.install(hooks)
        hooks._install_timeline_suppression()
        assert scheduling.Timeline().simulate() == 'simulated'
        assert calls == ['simulate']
    finally:
        modules.close()


def test_manual_travel_bypass_still_runs_original_timeline():
    modules, scheduling, calls = _runtime()
    try:
        guard = _load_guard()
        hooks = _hooks()
        hooks._simulation_bypass = True
        guard.install(hooks)
        hooks._install_timeline_suppression()
        assert scheduling.Timeline().simulate() == 'simulated'
        assert calls == ['simulate']
    finally:
        modules.close()
