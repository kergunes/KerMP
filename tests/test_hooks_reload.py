import importlib.util
import sys
import types
from pathlib import Path

_KERMP_MOD = Path(__file__).parents[1] / 'sims_mod_src' / 'kermp_mod'


class HooksSandbox:
    """Load ``kermp_mod.hooks`` against stub Sims modules so its apply/teardown
    logic can be exercised without the game runtime."""

    def __init__(self):
        self.saved = {}
        self.hooks = None
        self.services = None
        self.build_buy = None
        self.objects_system = None
        self.calls = []

    def _register(self, name, mod):
        self.saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    def __enter__(self):
        pkg = types.ModuleType('kermp_mod')
        pkg.__path__ = [str(_KERMP_MOD)]
        pkg.__package__ = 'kermp_mod'
        self._register('kermp_mod', pkg)

        for name in ('bridge_client', 'build_adapter'):
            spec = importlib.util.spec_from_file_location(
                'kermp_mod.%s' % name, _KERMP_MOD / (name + '.py'))
            mod = importlib.util.module_from_spec(spec)
            self._register('kermp_mod.%s' % name, mod)
            spec.loader.exec_module(mod)
            setattr(pkg, name, mod)

        services = types.ModuleType('services')
        services.current_zone_id = lambda: 1
        self.services = services
        self._register('services', services)

        objects = types.ModuleType('objects')
        objects.__path__ = []
        self._register('objects', objects)
        objects_system = types.ModuleType('objects.system')
        self.objects_system = objects_system
        self._register('objects.system', objects_system)
        objects.system = objects_system

        build_buy = types.ModuleType('build_buy')
        self.build_buy = build_buy
        self._register('build_buy', build_buy)

        def destroy(zone_id, object_id):
            self.calls.append(('destroy', zone_id, object_id))
            return True
        objects_system.c_api_destroy_object = destroy
        build_buy.c_api_set_object_location_ex = lambda *a, **k: True

        def modify_funds(amount, household_id, reason, zone_id):
            self.calls.append(('funds', amount, household_id, reason, zone_id))
            return True
        build_buy.c_api_modify_household_funds = modify_funds

        scheduling = types.ModuleType('scheduling')
        self.scheduling = scheduling
        self._register('scheduling', scheduling)
        sandbox = self

        class Timeline:
            def simulate(self):
                sandbox.calls.append(('simulate',))
                return 'simulated'

        scheduling.Timeline = Timeline

        protocolbuffers = types.ModuleType('protocolbuffers')
        protocolbuffers.__path__ = []
        self._register('protocolbuffers', protocolbuffers)
        consts = types.ModuleType('protocolbuffers.Consts_pb2')
        self.consts = consts
        self._register('protocolbuffers.Consts_pb2', consts)
        consts.MSG_OBJECT_IS_INTERACTABLE = 100
        consts.MSG_PIE_MENU_CREATE = 200
        consts.MSG_OBJECTS_VIEW_UPDATE = 300
        protocolbuffers.Consts_pb2 = consts

        server = types.ModuleType('server')
        server.__path__ = []
        self._register('server', server)
        client_mod = types.ModuleType('server.client')
        self.server_client = client_mod
        self._register('server.client', client_mod)
        server.client = client_mod

        class Client:
            def send_message(self, msg_id, msg):
                return 'sent'
        client_mod.Client = Client

        sims = types.ModuleType('sims')
        sims.__path__ = []
        self._register('sims', sims)
        sim_mod = types.ModuleType('sims.sim')
        self.sim_mod = sim_mod
        self._register('sims.sim', sim_mod)
        sims.sim = sim_mod

        class Sim:
            def __init__(self):
                self.id = 42
                self.sim_info = types.SimpleNamespace(id=77)

            def push_super_affordance(self, affordance=None, *args, **kwargs):
                sandbox.calls.append(('push', affordance))
                return 'result'
        sim_mod.Sim = Sim

        interactions = types.ModuleType('interactions')
        interactions.__path__ = []
        self._register('interactions', interactions)
        aop_mod = types.ModuleType('interactions.aop')
        self.aop_mod = aop_mod
        self._register('interactions.aop', aop_mod)
        interactions.aop = aop_mod

        class EnqueueResult:
            def __init__(self, test_result, execute_result):
                self.test_result = test_result
                self.execute_result = execute_result

        class AffordanceObjectPair:
            def __init__(self, affordance, target=None, kwargs=None):
                self.affordance = affordance
                self.target = target
                self._kwargs = kwargs or {}

            def test_and_execute(self, context, **kwargs):
                sandbox.calls.append(('aop_original',))
                return 'original'

        aop_mod.EnqueueResult = EnqueueResult
        aop_mod.AffordanceObjectPair = AffordanceObjectPair

        spec = importlib.util.spec_from_file_location('kermp_mod.hooks', _KERMP_MOD / 'hooks.py')
        hooks = importlib.util.module_from_spec(spec)
        self._register('kermp_mod.hooks', hooks)
        spec.loader.exec_module(hooks)
        self.hooks = hooks
        return self

    def __exit__(self, *exc):
        for name, prev in self.saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def test_destroy_apply_never_mutates_funds():
    with HooksSandbox() as sandbox:
        result = sandbox.hooks._apply_object_operation(
            {'op': 'object.destroy', 'data': {'object_id': 5, 'zone_id': 1}})
        assert result is True
        assert sandbox.calls == [('destroy', 1, 5)]


def test_failed_destroy_has_no_economic_effect():
    with HooksSandbox() as sandbox:
        def fail(zone_id, object_id):
            sandbox.calls.append(('destroy', zone_id, object_id))
            raise ValueError('destroy failed')
        sandbox.objects_system.c_api_destroy_object = fail
        try:
            sandbox.hooks._apply_object_operation(
                {'op': 'object.destroy', 'data': {'object_id': 5, 'zone_id': 1}})
            raised = False
        except ValueError:
            raised = True
        assert raised
        assert [c for c in sandbox.calls if c[0] == 'funds'] == []


def test_object_value_reports_zero_instead_of_falling_back():
    with HooksSandbox() as sandbox:
        zero_value = types.SimpleNamespace(current_value=0, catalog_value=120)
        assert sandbox.hooks._object_value(zero_value) == 0


def test_wall_callback_teardown_removes_registration():
    with HooksSandbox() as sandbox:
        fake_zone = types.SimpleNamespace(wall_contour_update_callbacks=[])
        sandbox.services.current_zone = lambda: fake_zone
        hooks = sandbox.hooks

        info = hooks._install_wall_contour_callback()
        assert info['registered'] is True
        assert len(fake_zone.wall_contour_update_callbacks) == 1

        errors = hooks.teardown()
        assert errors == []
        assert fake_zone.wall_contour_update_callbacks == []

        hooks._install_wall_contour_callback()
        assert len(fake_zone.wall_contour_update_callbacks) == 1


def test_teardown_restores_patch_and_second_install_wraps_once():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._build_hook_info['move_hook'] = {'available': True}

        original = sandbox.build_buy.c_api_set_object_location_ex
        assert hooks._install_wrapper('move_hook', hooks._wrap_move) is True
        assert getattr(sandbox.build_buy.c_api_set_object_location_ex, '_kermp_wrapped', False)
        assert len(hooks._patches) == 1

        errors = hooks.teardown()
        assert errors == []
        assert sandbox.build_buy.c_api_set_object_location_ex is original
        assert len(hooks._patches) == 0

        # second install wraps exactly once, no double wrapping
        hooks._build_hook_info['move_hook'] = {'available': True}
        assert hooks._install_wrapper('move_hook', hooks._wrap_move) is True
        assert getattr(sandbox.build_buy.c_api_set_object_location_ex, '_kermp_wrapped', False)
        assert len(hooks._patches) == 1


def test_teardown_is_idempotent():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        assert hooks.teardown() == []
        assert hooks.teardown() == []


def test_client_simulation_suppressed_but_travel_bypasses():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        assert hooks._install_timeline_suppression() is True
        simulate = sandbox.scheduling.Timeline.simulate
        assert getattr(simulate, '_kermp_wrapped', False)
        timeline = sandbox.scheduling.Timeline()

        # normal client gameplay: suppressed, original never called
        assert simulate(timeline) is None
        assert sandbox.calls == []

        # travel bypass: original runs
        hooks.set_simulation_bypass(True)
        assert simulate(timeline) == 'simulated'
        assert sandbox.calls == [('simulate',)]


def test_host_simulation_never_suppressed():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'host'
        assert hooks._install_timeline_suppression() is True
        simulate = sandbox.scheduling.Timeline.simulate
        timeline = sandbox.scheduling.Timeline()
        assert simulate(timeline) == 'simulated'
        assert sandbox.calls == [('simulate',)]


def test_simulation_suppression_teardown_restores():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        assert hooks._install_timeline_suppression() is True
        original = sandbox.scheduling.Timeline.simulate._kermp_original
        errors = hooks.teardown()
        assert errors == []
        assert sandbox.scheduling.Timeline.simulate is original
        # reinstall wraps exactly once
        hooks._sidecar_role = 'client'
        assert hooks._install_timeline_suppression() is True
        assert getattr(sandbox.scheduling.Timeline.simulate, '_kermp_wrapped', False)


def test_message_classification_local_only_vs_replicate():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        assert hooks._classify_message(100) == 'local_only'
        assert hooks._classify_message(200) == 'local_only'
        assert hooks._classify_message(300) == 'replicate'
        assert hooks._classify_message(999) == 'replicate'


def test_host_message_capture_replicates_non_local_only():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'host'
        assert hooks._install_game_message_capture() is True

        class Msg:
            def SerializeToString(self):
                return b'data'

        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        sandbox.server_client.Client().send_message(300, Msg())
        sandbox.server_client.Client().send_message(100, Msg())
        status = hooks.message_capture_status()
        assert status['replicated'] == 1
        assert status['dropped_local'] == 1
        assert any(t == 'game.raw_message' for t, _ in emitted)


def test_client_interaction_forwarded_not_executed():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        assert hooks._install_interaction_interception() is True
        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        affordance = types.SimpleNamespace(guid64=123)
        target = types.SimpleNamespace(id=7)
        sim = sandbox.sim_mod.Sim()
        result = sim.push_super_affordance(affordance, target)
        assert result is None
        assert sandbox.calls == []
        requests = [p for t, p in emitted if t == 'interaction.request']
        assert len(requests) == 1
        assert requests[0]['affordance_id'] == '123'
        assert requests[0]['target_id'] == '7'
        assert requests[0]['sim_id'] == '77'
        assert requests[0]['position'] is None
        assert requests[0]['interaction_kwargs'] == {}


def test_client_position_interaction_carries_pick_location():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        assert hooks._install_interaction_interception() is True
        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        sim = sandbox.sim_mod.Sim()
        target = types.SimpleNamespace(position=types.SimpleNamespace(x=1, y=2, z=3))
        assert sim.push_super_affordance(types.SimpleNamespace(guid64=123), target) is None
        request = next(p for t, p in emitted if t == 'interaction.request')
        assert request['target_id'] == '0'
        assert request['position']['translation'] == [1.0, 2.0, 3.0]


def test_client_aop_player_entrypoint_is_captured_without_local_execute():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        assert hooks._install_aop_interception() is True
        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        sim = sandbox.sim_mod.Sim()
        context = types.SimpleNamespace(
            source=types.SimpleNamespace(name='SOURCE_PIE_MENU'), sim=sim,
            pick=types.SimpleNamespace(position=types.SimpleNamespace(x=4, y=5, z=6)))
        aop = sandbox.aop_mod.AffordanceObjectPair(types.SimpleNamespace(guid64=321), None,
                                                    {'picked_item_ids': [9]})
        result = aop.test_and_execute(context)
        assert result.test_result is True
        assert sandbox.calls == []
        request = next(p for t, p in emitted if t == 'interaction.request')
        assert request['affordance_id'] == '321'
        assert request['sim_id'] == '77'
        assert request['position']['translation'] == [4.0, 5.0, 6.0]
        assert hooks.aop_status()['user_seen'] == 1


def test_position_proxy_with_numeric_id_is_not_sent_as_world_object():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        proxy = types.SimpleNamespace(id=14982,
                                      position=types.SimpleNamespace(x=1, y=2, z=3))
        sandbox.services.object_manager = lambda: types.SimpleNamespace(get=lambda _id: None)
        descriptor = hooks._classify_interaction_target(proxy, types.SimpleNamespace(pick=None))
        assert descriptor['target_kind'] == 'position'
        assert descriptor['target_id'] == '0'
        assert descriptor['position']['translation'] == [1.0, 2.0, 3.0]


def test_managed_object_and_sim_targets_have_distinct_descriptors():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        obj = types.SimpleNamespace(id=42)
        sandbox.services.object_manager = lambda: types.SimpleNamespace(get=lambda object_id: obj if object_id == 42 else None)
        object_descriptor = hooks._classify_interaction_target(obj, types.SimpleNamespace(pick=None))
        assert object_descriptor['target_kind'] == 'object'
        assert object_descriptor['target_id'] == '42'
        sim_target = types.SimpleNamespace(id=77, sim_info=types.SimpleNamespace(id=77))
        sim_descriptor = hooks._classify_interaction_target(sim_target, types.SimpleNamespace(pick=None))
        assert sim_descriptor['target_kind'] == 'sim'
        assert sim_descriptor['target_sim_id'] == '77'


def test_unknown_temporary_target_fails_closed():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        sandbox.services.object_manager = lambda: types.SimpleNamespace(get=lambda _id: None)
        descriptor = hooks._classify_interaction_target(types.SimpleNamespace(id=900),
                                                         types.SimpleNamespace(pick=None))
        assert descriptor['target_kind'] == 'unknown'


def test_terrain_point_descriptor_rebuilds_native_surface_proxy():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        sims4 = types.ModuleType('sims4')
        sims4.__path__ = []
        sandbox._register('sims4', sims4)
        math = types.ModuleType('sims4.math')
        sandbox._register('sims4.math', math)
        sims4.math = math

        class Vector3:
            def __init__(self, x, y, z):
                self.x, self.y, self.z = x, y, z

        class Transform:
            def __init__(self, position):
                self.translation = position

        class Location:
            def __init__(self, transform, routing_surface):
                self.transform = transform
                self.routing_surface = routing_surface

        math.Vector3, math.Transform, math.Location = Vector3, Transform, Location
        routing = types.ModuleType('routing')
        sandbox._register('routing', routing)

        class SurfaceIdentifier:
            def __init__(self, primary_id, secondary_id, surface_type):
                self.primary_id, self.secondary_id, self.type = primary_id, secondary_id, surface_type

        routing.SurfaceIdentifier = SurfaceIdentifier
        sandbox.services.__path__ = []
        terrain_service = types.ModuleType('services.terrain_service')
        sandbox._register('services.terrain_service', terrain_service)
        calls = []

        def create_surface_proxy_from_location(location):
            calls.append(location)
            return types.SimpleNamespace(native_kind='TerrainPoint', location=location)

        terrain_service.create_surface_proxy_from_location = create_surface_proxy_from_location
        target = hooks._deserialize_interaction_position_target({
            'translation': [1.0, 2.0, 3.0],
            'routing_surface': {'primary_id': 4, 'secondary_id': 1, 'type': 0}})
        assert target.native_kind == 'TerrainPoint'
        assert calls[0].transform.translation.x == 1.0
        assert calls[0].routing_surface.secondary_id == 1


def test_host_interaction_executes_locally():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'host'
        assert hooks._install_interaction_interception() is True
        affordance = types.SimpleNamespace(guid64=123)
        sim = sandbox.sim_mod.Sim()
        result = sim.push_super_affordance(affordance)
        assert result == 'result'
        assert sandbox.calls == [('push', affordance)]


def test_client_interaction_unresolvable_affordance_is_dropped():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        assert hooks._install_interaction_interception() is True
        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        sim = sandbox.sim_mod.Sim()
        result = sim.push_super_affordance(types.SimpleNamespace())  # no guid64/id
        assert result is None
        assert not any(t == 'interaction.request' for t, _ in emitted)
        assert hooks.interaction_status()['dropped'] == 1


def test_native_push_capture_forwards_typed_object_command_without_aop_fallback():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        argument_helpers = types.ModuleType('server_commands.argument_helpers')
        server_commands = types.ModuleType('server_commands')
        server_commands.__path__ = []
        sandbox._register('server_commands', server_commands)
        sandbox._register('server_commands.argument_helpers', argument_helpers)
        server_commands.argument_helpers = argument_helpers

        sim = sandbox.sim_mod.Sim()
        target = types.SimpleNamespace(id=88)
        sandbox.services.object_manager = lambda: types.SimpleNamespace(get=lambda object_id: target if object_id == 88 else None)
        argument_helpers.get_optional_target = lambda opt_sim, connection, notify_failure=False: sim
        affordance = types.SimpleNamespace(guid64=123)
        opt_target = types.SimpleNamespace(get_target=lambda: target)
        emitted = []
        hooks.bridge.emit = lambda name, payload: emitted.append((name, payload)) or True

        result = hooks._capture_native_push('interactions.push', lambda *args: 'fallback',
                                            affordance, opt_target, object(), 'High', 'SOURCE_PIE_MENU', 7)
        assert result.test_result is True
        assert emitted[0][0] == 'interaction.command'
        command = emitted[0][1]['command']
        assert command['name'] == 'interactions.push'
        assert command['affordance_id'] == '123'
        assert command['target']['target_kind'] == 'object'
        assert hooks.command_status()['fallback'] == 0


def test_native_choice_capture_preserves_pick_arguments_for_host_replay():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        sim = sandbox.sim_mod.Sim()
        sandbox.services.client_manager = lambda: types.SimpleNamespace(
            get=lambda connection: types.SimpleNamespace(active_sim=sim))
        emitted = []
        hooks.bridge.emit = lambda name, payload: emitted.append((name, payload)) or True

        assert hooks._capture_native_choice('interactions.choices', lambda *args: 'local',
                                            [0, 'PICK_TERRAIN', 1.0, 2.0, 3.0], 7, True) == 'local'
        command = emitted[0][1]['command']
        assert command['name'] == 'interactions.choices'
        assert command['args'][1] == 'PICK_TERRAIN'
        assert emitted[0][1]['sim_id'] == '77'


def test_native_choice_capture_uses_native_pick_type_for_local_ea_call():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        sim = sandbox.sim_mod.Sim()
        native_pick_type = object()
        sandbox.services.client_manager = lambda: types.SimpleNamespace(
            get=lambda connection: types.SimpleNamespace(active_sim=sim))
        hooks.bridge.emit = lambda name, payload: True
        local_calls = []

        hooks._capture_native_choice('interactions.has_choices',
                                     lambda *args: local_calls.append(args) or 'local',
                                     [0, 'PICK_TERRAIN'], 7, True,
                                     [0, native_pick_type])
        assert local_calls[0][1] is native_pick_type
        assert local_calls[0][-1] == 7


def test_native_interaction_started_requires_real_start_signal():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        emitted = []
        hooks.bridge.emit = lambda name, payload: emitted.append((name, payload)) or True
        interaction = types.SimpleNamespace(id=901)
        hooks._native_interaction_requests['901'] = {
            'request_id': 'req-1', 'sim_id': '77', 'name': 'interactions.select'}

        hooks._native_interaction_started(interaction)

        assert emitted == [('interaction.started', {
            'request_id': 'req-1', 'sim_id': '77', 'interaction_id': '901',
            'command': 'interactions.select',
            'signal': 'Interaction._trigger_interaction_start_event'})]
        assert hooks.command_status()['interaction_started'] == 1
        assert hooks.command_status()['by_name']['interactions.select']['interaction_started'] == 1
        hooks._native_interaction_started(interaction)
        assert len(emitted) == 1


def test_headless_remote_client_skips_stock_distributor_registration_and_tears_down():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        calls = []

        class Selectable:
            def add_watcher(self, client, callback): calls.append(('watch_add', client.id))
            def remove_watcher(self, client): calls.append(('watch_remove', client.id))

        class FakeClient:
            def __init__(self, client_id, account, household_id):
                self.id, self._account, self._household_id = client_id, account, household_id
                self._selectable_sims = []
                self.selectable_sims = Selectable()
                self.active_sim = None
                self.active = True
            def on_add(self): calls.append(('vanilla_add', self.id))
            def on_remove(self): calls.append(('vanilla_remove', self.id))
            def on_sim_added_to_skewer(self, sim_info): calls.append(('sim_add', self.id))
            def on_sim_removed_from_skewer(self, sim_info): calls.append(('sim_remove', self.id))
            def send_selectable_sims_update(self): calls.append(('selectable_update', self.id))
            def send_message(self, msg_id, msg): calls.append(('omega_send', self.id, msg_id))
            def _set_active_sim_without_field_distribution(self, info): self.active_sim = info

        sandbox.server_client.Client = FakeClient
        account_mod = types.ModuleType('server.account')
        sandbox._register('server.account', account_mod)

        class Account:
            def __init__(self, account_id, persona_name): self.id = account_id
            def register_client(self, client): calls.append(('account_add', client.id))
            def unregister_client(self, client): calls.append(('account_remove', client.id))
        account_mod.Account = Account

        class Manager:
            def __init__(self): self.objects = {}
            def get(self, client_id): return self.objects.get(client_id)
            def create_client(self, client_id, account, household_id):
                client = FakeClient(client_id, account, household_id)
                self.objects[client_id] = client
                client.on_add()
                return client
            def remove(self, client):
                client.on_remove()
                self.objects.pop(client.id, None)
        manager = Manager()
        sandbox.services.client_manager = lambda: manager
        remote_sim = types.SimpleNamespace(sim_info=types.SimpleNamespace(id=77, household_id=10))
        host_client = FakeClient(1, Account(1, 'host'), 10)
        host_client.on_add()

        remote = hooks._remote_client_for('p2', remote_sim)
        assert remote.id in manager.objects
        assert remote.active_sim is remote_sim.sim_info
        assert ('vanilla_add', remote.id) not in calls
        assert ('vanilla_add', 1) in calls
        assert not any(item[0] == 'omega_send' and item[1] == remote.id for item in calls)

        hooks._teardown_command_proxy()
        assert remote.id not in manager.objects
        assert ('vanilla_remove', remote.id) not in calls
        assert ('account_remove', remote.id) in calls


def test_active_sim_is_published_once_after_roster_is_available():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        info = types.SimpleNamespace(id=77, full_name='Active Sim',
                                     get_sim_instance=lambda allow_hidden_flags=True: object())
        sandbox.services.get_first_client = lambda: types.SimpleNamespace(active_sim_info=info)
        sandbox.services.sim_info_manager = lambda: types.SimpleNamespace(get_all=lambda: [info])
        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        assert hooks._publish_active_sim_if_ready() is True
        assert hooks._publish_active_sim_if_ready() is True
        assert [t for t, _ in emitted] == ['sims.state', 'sim.select', 'sims.state']
        assert emitted[1][1]['sim_id'] == '77'


def test_active_sim_is_not_published_before_valid_roster_entry():
    with HooksSandbox() as sandbox:
        hooks = sandbox.hooks
        hooks._sidecar_role = 'client'
        sandbox.services.get_first_client = lambda: types.SimpleNamespace(
            active_sim_info=types.SimpleNamespace(id=77))
        sandbox.services.sim_info_manager = lambda: types.SimpleNamespace(get_all=lambda: [])
        emitted = []
        hooks.bridge.emit = lambda t, p: emitted.append((t, p)) or True
        assert hooks._publish_active_sim_if_ready() is False
        assert [t for t, _ in emitted] == ['sims.state']
