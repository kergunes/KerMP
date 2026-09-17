import json

import sims4.commands
import services

from . import hooks
from .hooks import (bridge, probe_wall_contours, wall_event_probe, enumerate_sims,
                    enumerate_objects, enumerate_affordances, inspect_distributor_boundary)
from .hooks import _serialize_transform, _object_value, _object_household_id
from .build_adapter import adapter, build_buy_surface


def _native_module():
    try:
        import KerMPNative
        return KerMPNative
    except Exception:
        return None


def _out(connection):
    return sims4.commands.CheatOutput(connection)


@sims4.commands.Command('kermp.status', command_type=sims4.commands.CommandType.Live)
def kermp_status(_connection=None):
    zone_id = 0
    try:
        zone_id = services.current_zone_id()
    except Exception:
        pass
    _out(_connection)('KerMP loaded. zone_id=%s bridge_connected=%s' % (zone_id, bool(bridge.sock)))


@sims4.commands.Command('kermp.core.status', command_type=sims4.commands.CommandType.Live)
def kermp_core_status(_connection=None):
    out = _out(_connection)
    sims = enumerate_sims()
    active = sims[0]['sim_id'] if len(sims) == 1 else 'unknown'
    out('role=%s player_id=local active_sim_id=%s connected_players=unknown' %
        (hooks._sidecar_role or 'unknown', active))
    out('interaction_requests=unknown interaction_started=unknown view_updates_sent=%s' %
        getattr(hooks, '_view_updates_sent', 0))
    out('view_updates_received=%s last_view_update_size=%s last_view_update_msg_id=%s' %
        (getattr(hooks, '_view_updates_received', 0), getattr(hooks, '_last_view_update_size', 0),
         getattr(hooks, '_last_view_update_msg_id', 'unknown')))
    out('travel_state=%s travel_epoch=%s buffered_view_updates=%s timeline_mode=normal' %
        ('pending' if hooks._pending_travel_txn else 'idle', hooks._travel_epoch,
        len(hooks._travel_buffer)))


@sims4.commands.Command('kermp.simulation.status', command_type=sims4.commands.CommandType.Live)
def kermp_simulation_status(_connection=None):
    status = hooks.simulation_status()
    _out(_connection)('role=%s timeline_suppression_available=%s installed=%s local_simulation_enabled=%s clock_source=%s last_error=%s' %
        (status.get('role'), status.get('timeline_suppression_available'), status.get('installed'),
         status.get('local_simulation_enabled'), status.get('clock_source'), status.get('last_error') or 'none'))


@sims4.commands.Command('kermp.play.status', command_type=sims4.commands.CommandType.Live)
def kermp_play_status(_connection=None):
    out = _out(_connection)
    role = hooks._sidecar_role or 'unknown'
    sim = hooks.simulation_status()
    clock = hooks.clock_status()
    cap = hooks.message_capture_status()
    inter = hooks.interaction_status()
    zone_id = 0
    try:
        zone_id = services.current_zone_id()
    except Exception:
        pass
    try:
        import omega
        omega_send = bool(callable(getattr(omega, 'send', None)))
    except Exception:
        omega_send = False
    out('ROLE=%s' % role)
    out('BRIDGE=%s zone_id=%s' % ('connected' if bool(bridge.sock) else 'disconnected', zone_id))
    out('SIM_AUTHORITY suppression_installed=%s available=%s local_enabled=%s bypass=%s error=%s' %
        (sim.get('installed'), sim.get('timeline_suppression_available'),
         sim.get('local_simulation_enabled'), sim.get('bypass'), sim.get('last_error') or 'none'))
    out('CLOCK hooks_installed=%s source=%s requests=%s applied=%s error=%s' %
        (clock.get('installed'), sim.get('clock_source'), clock.get('requests'),
         clock.get('applied'), clock.get('last_error') or 'none'))
    out('OMEGA send_available=%s' % omega_send)
    out('CAPTURE installed=%s observed=%s replicated=%s dropped_local=%s dropped_local_ops=%s error=%s' %
        (getattr(hooks, '_game_message_capture_installed', False), cap.get('observed'),
         cap.get('replicated'), cap.get('dropped_local'), cap.get('dropped_local_ops'),
         (cap.get('last_error') or 'none').splitlines()[0] if cap.get('last_error') else 'none'))
    out('INTERACTION intercepted=%s sent=%s accepted=%s rejected=%s started=%s last_affordance=%s last_target=%s last_sim=%s error=%s' %
        (inter.get('forwarded'), inter.get('sent'), inter.get('accepted'), inter.get('rejected'), inter.get('started'),
         inter.get('last_affordance_id'), inter.get('last_target_id'), inter.get('last_sim_id'),
         (inter.get('last_error') or 'none').splitlines()[0] if inter.get('last_error') else 'none'))
    out('RX view_updates_received=%s TX view_updates_sent=%s' %
        (getattr(hooks, '_view_updates_received', 0), getattr(hooks, '_view_updates_sent', 0)))
    out('LOCAL_ACTIVE_SIM=%s' % (hooks._active_sim_id() or 'none'))
    out('AUTHORITATIVE_CONTROLLED_SIM=%s' % (hooks._authoritative_sim_id or 'none'))
    out('BUILD_MODE build_owner=%s capture_available=%s apply_available=%s' %
        (getattr(adapter, 'last_local_operation', None) and adapter.last_local_operation.get('op', 'none') or 'none',
         'capture' in adapter.capabilities(), 'apply' in adapter.capabilities()))


@sims4.commands.Command('kermp.distributor.status', command_type=sims4.commands.CommandType.Live)
def kermp_distributor_status(_connection=None):
    result = inspect_distributor_boundary()
    out = _out(_connection)
    out('modules=%s client_type=%s omega_type=%s omega_send_callable=%s capture_installed=%s error=%s omega_error=%s' %
        (','.join(result.get('modules') or []) or 'none',
         result.get('client_type') or 'none', result.get('omega_type') or 'none',
         result.get('omega_send_callable'), result.get('capture_installed'),
         result.get('error') or 'none', result.get('omega_error') or 'none'))
    out('global_omega_type=%s global_omega_send_callable=%s' %
        (result.get('global_omega_type') or 'none', result.get('global_omega_send_callable')))
    out('distributor_methods=%s' % (','.join(result.get('distributor') or []) or 'none'))
    out('distributor_instance_methods=%s' %
        (','.join(result.get('distributor_instance') or []) or 'none'))
    out('client_methods=%s' % (','.join(result.get('client_methods') or []) or 'none'))
    out('omega_methods=%s' % (','.join(result.get('client_omega') or []) or 'none'))
    out('view_update_consts=%s' % (result.get('consts') or {}))
    for name, signature in sorted((result.get('distributor_signatures') or {}).items()):
        out('distributor.%s signature=%s' % (name, signature))


@sims4.commands.Command('kermp.sims', command_type=sims4.commands.CommandType.Live)
def kermp_sims(_connection=None):
    sims = enumerate_sims()
    bridge.emit('sims.state', {'sims': sims, 'active_sim_id': hooks._active_sim_id()})
    out = _out(_connection)
    for sim in sims:
        out('sim_id=%s name=%s controllers=%s' %
            (sim['sim_id'], sim['name'], ','.join(hooks._authoritative_controllers.get(sim['sim_id'], [])) or 'none'))
    if not sims:
        out('no loaded Sims found')


@sims4.commands.Command('kermp.sim.select', command_type=sims4.commands.CommandType.Live)
def kermp_sim_select(sim_id: str, _connection=None):
    ok = bridge.emit('sim.select', {'sim_id': str(sim_id), 'local': True})
    _out(_connection)('KerMP sim selection requested sim_id=%s sent=%s' % (sim_id, ok))


@sims4.commands.Command('kermp.interact', command_type=sims4.commands.CommandType.Live)
def kermp_interact(sim_id: str, affordance_id: str, target_id: str = '0', _connection=None):
    ok = bridge.emit('interaction.request', {'request_id': 'local-%s' % __import__('uuid').uuid4().hex,
        'player_id': 'local', 'sim_id': str(sim_id), 'affordance_id': str(affordance_id),
        'target_id': str(target_id)})
    _out(_connection)('KerMP interaction requested sim=%s affordance=%s target=%s sent=%s' %
                      (sim_id, affordance_id, target_id, ok))


@sims4.commands.Command('kermp.objects', command_type=sims4.commands.CommandType.Live)
def kermp_objects(_connection=None):
    out = _out(_connection)
    objects = enumerate_objects()
    for obj in objects:
        out('object_id=%s name=%s type=%s' % (obj['object_id'], obj['name'], obj['type']))
    if not objects:
        out('no loaded objects found')


def _inspect_value(value, fallback='unknown'):
    try:
        value = value() if callable(value) else value
    except Exception:
        return fallback
    return fallback if value is None else value


def _inspect_attr(obj, name, fallback='unknown'):
    try:
        return _inspect_value(getattr(obj, name), fallback)
    except Exception:
        return fallback


def _inspect_mapping_value(value, name, fallback=None):
    try:
        return value[name]
    except Exception:
        return fallback


def _inspect_components(value, names):
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= len(names):
        return [value[index] for index in range(len(names))]
    if isinstance(value, dict):
        result = [_inspect_mapping_value(value, name) for name in names]
    else:
        result = [_inspect_attr(value, name, None) for name in names]
    return None if any(item is None for item in result) else result


def _inspect_transform(obj):
    location = _inspect_attr(obj, 'location', None)
    serialized = None
    try:
        serialized = _serialize_transform(location)
    except Exception:
        pass
    if not isinstance(serialized, dict):
        serialized = {}
    position = _inspect_mapping_value(serialized, 'translation')
    orientation = _inspect_mapping_value(serialized, 'orientation')
    routing_surface = _inspect_mapping_value(serialized, 'routing_surface')
    if position is None:
        position = _inspect_components(_inspect_attr(obj, 'position', None), ('x', 'y', 'z'))
    if orientation is None:
        orientation = _inspect_components(_inspect_attr(obj, 'orientation', None), ('x', 'y', 'z', 'w'))
    if routing_surface is None:
        routing_surface = _inspect_attr(obj, 'routing_surface', 'unknown')
    return (position or ['unknown', 'unknown', 'unknown'],
            orientation or ['unknown', 'unknown', 'unknown', 'unknown'],
            routing_surface)


def _inspect_object_lines(obj):
    definition = _inspect_attr(obj, 'definition', None)
    definition_id = _inspect_attr(definition, 'id', None)
    if definition_id is None:
        definition_id = _inspect_attr(obj, 'definition_id', 'unknown')
    name = _inspect_attr(obj, 'name', None)
    if name is None and definition is not None:
        name = _inspect_attr(definition, 'name', None)
    if name is None and definition is not None:
        name = _inspect_attr(definition, 'display_name', 'unknown')
    position, orientation, routing_surface = _inspect_transform(obj)
    parent = _inspect_attr(obj, 'parent', None)
    parent_id = _inspect_attr(parent, 'id', None) if parent is not None else None
    if parent_id is None:
        parent_id = _inspect_attr(obj, 'parent_id', 'none')
    if isinstance(routing_surface, (dict, list, tuple)):
        routing_surface = json.dumps(routing_surface, sort_keys=True, separators=(',', ':'))
    return [
        'object_id=%s' % _inspect_attr(obj, 'id'),
        'definition_id=%s' % definition_id,
        'name=%s' % _inspect_value(name),
        'type=%s' % type(obj).__name__,
        'scale=%s' % _inspect_attr(obj, 'scale'),
        'value=%s' % _inspect_value(_object_value(obj)),
        'household_id=%s' % _inspect_value(_object_household_id(obj)),
        'position=%s' % ','.join(str(item) for item in position),
        'orientation=%s' % ','.join(str(item) for item in orientation),
        'parent_id=%s' % parent_id,
        'routing_surface=%s' % routing_surface,
    ]


@sims4.commands.Command('kermp.object.inspect', command_type=sims4.commands.CommandType.Live)
def kermp_object_inspect(object_id: str, _connection=None):
    out = _out(_connection)
    object_id = str(object_id)
    try:
        obj = services.object_manager().get(int(object_id))
    except Exception as exc:
        out('object_inspect_failed=%s' % exc)
        return
    if obj is None:
        out('object_not_found=%s' % object_id)
        return
    try:
        for line in _inspect_object_lines(obj):
            out(line)
    except Exception as exc:
        out('object_inspect_failed=%s' % exc)


@sims4.commands.Command('kermp.affordances', command_type=sims4.commands.CommandType.Live)
def kermp_affordances(object_id: str, _connection=None):
    out = _out(_connection)
    try:
        affordances = enumerate_affordances(object_id)
        for item in affordances:
            out('affordance_id=%s name=%s' % (item['affordance_id'], item['name']))
        if not affordances:
            out('no exposed affordances found object_id=%s' % object_id)
    except Exception as exc:
        out('affordance lookup failed reason=%s' % exc)


@sims4.commands.Command('kermp.ping', command_type=sims4.commands.CommandType.Live)
def kermp_ping(_connection=None):
    ok = bridge.emit('game.ping', {'zone_id': services.current_zone_id()})
    _out(_connection)('KerMP bridge ping sent=%s' % ok)


@sims4.commands.Command('kermp.travel.request', command_type=sims4.commands.CommandType.Live)
def kermp_travel_request(zone_id: int, _connection=None):
    ok = bridge.emit('travel.request', {'zone_id': str(zone_id), 'actor_ids': []})
    _out(_connection)('KerMP travel request sent=%s zone=%s' % (ok, zone_id))


@sims4.commands.Command('kermp.travel.status', command_type=sims4.commands.CommandType.Live)
def kermp_travel_status(_connection=None):
    out = _out(_connection)
    pending = hooks._pending_travel_txn
    out('role=%s txn_id=%s epoch=%s phase=%s current_zone=%s target_zone=%s' %
        (hooks._sidecar_role or 'unknown', pending or 'none', hooks._travel_epoch,
         'waiting_zone_ready' if pending else 'idle', hooks._current_zone_id(),
         hooks._travel_api_info.get('zone_id', 'unknown')))
    out('local_zone_loaded=%s batch_open=%s batch_complete=%s buffered_updates=%s selected_sim_snapshot=%s selected_sim_restored=%s last_error=%s' %
        (hooks._travel_local_zone_loaded, hooks._travel_buffering,
         hooks._travel_batch_complete, len(hooks._travel_buffer),
         hooks._travel_selected_sim_id or 'none', hooks._travel_selected_sim_restored,
         hooks._travel_last_error or 'none'))
    out('travel_api_found=%s travel_function_signature=%s' %
        (bool(hooks._travel_api_info), hooks._travel_api_info.get('signature', 'unknown')))


@sims4.commands.Command('kermp.zones', command_type=sims4.commands.CommandType.Live)
def kermp_zones(_connection=None):
    out = _out(_connection)
    found = []
    try:
        persistence = services.get_persistence_service()
        for name in ('zone_proto_buffs', '_zone_proto_buffs', 'zones'):
            value = getattr(persistence, name, None)
            if isinstance(value, dict):
                found = list(value.items())[:40]
                break
    except Exception:
        pass
    if not found:
        out('zone_id=%s name=current (persistence enumeration unavailable)' % services.current_zone_id())
        return
    for zone_id, proto in found:
        out('zone_id=%s name=%s world=%s' %
            (zone_id, getattr(proto, 'name', 'unknown'), getattr(proto, 'world_id', 'unknown')))


@sims4.commands.Command('kermp.wall.test', command_type=sims4.commands.CommandType.Live)
def kermp_wall_test(x1: float, y1: float, x2: float, y2: float, level: int = 0, _connection=None):
    ok = bridge.emit('build.operation', {
        'op': 'wall.create',
        'data': {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'level': level},
    })
    _out(_connection)('KerMP synthetic wall event sent=%s' % ok)


@sims4.commands.Command('kermp.build.status', command_type=sims4.commands.CommandType.Live)
def kermp_build_status(_connection=None):
    out = _out(_connection)
    out('capture_available=%s apply_available=%s suppression_active=%s' %
        ('capture' in adapter.capabilities(), 'apply' in adapter.capabilities(),
         adapter.applying_remote))
    out('last_local_operation=%s' % adapter.last_local_operation)
    out('last_remote_operation=%s' % adapter.last_remote_operation)
    surface = build_buy_surface()
    out('build_buy_module=%s candidates=%s' %
        (surface['module_available'], ','.join(surface['candidates'])))
    event = wall_event_probe()
    out('wall_callback_available=%s registered=%s events=%s' %
        (event['attribute_exists'], event['registered'], event['event_count']))
    object_status = hooks.build_object_status()
    hook_map = object_status.get('hooks') or {}
    for name in ('move_hook', 'create_hook', 'destroy_hook', 'definition_hook',
                 'scale_hook', 'funds_hook', 'parent_hook', 'clear_parent_hook'):
        item = hook_map.get(name) or {}
        out('%s available=%s installed=%s signature=%s' %
            (name, item.get('available', False), item.get('installed', False),
             item.get('signature') or 'unavailable'))
    status = adapter.status()
    counts = status.get('operation_counts') or {}
    for name in ('object.move', 'object.definition', 'object.destroy', 'object.create',
                 'object.scale', 'object.set_parent', 'object.clear_parent', 'funds.modify'):
        out('%s=%s' % (name, counts.get(name, 0)))
    out('captured_total=%s suppressed_remote_echo=%s capture_errors=%s' %
        (status.get('captured_total'), status.get('suppressed_remote_echo'), status.get('capture_errors')))
    local = status.get('last_local_operation') or {}
    remote = status.get('last_remote_operation') or {}
    out('last_local_type=%s last_local_object_id=%s last_local_op_id=%s' %
        (local.get('op', 'none'), (local.get('data') or {}).get('object_id', 'none'), local.get('op_id', 'none')))
    out('last_remote_type=%s last_error=%s' %
        (remote.get('op', 'none'), status.get('last_error') or 'none'))


@sims4.commands.Command('kermp.build.last-create', command_type=sims4.commands.CommandType.Live)
def kermp_build_last_create(_connection=None):
    operation = adapter.last_operation_by_type.get('object.create')
    _out(_connection)('last_create=%s' % (json.dumps(operation, sort_keys=True) if operation else 'none'))


@sims4.commands.Command('kermp.build.object.status', command_type=sims4.commands.CommandType.Live)
def kermp_build_object_status(_connection=None):
    return kermp_build_status(_connection)


@sims4.commands.Command('kermp.build.object.reset', command_type=sims4.commands.CommandType.Live)
def kermp_build_object_reset(_connection=None):
    adapter.reset_diagnostics()
    _out(_connection)('KerMP Build/Buy diagnostics reset')


@sims4.commands.Command('kermp.native.status', command_type=sims4.commands.CommandType.Live)
def kermp_native_status(_connection=None):
    out = _out(_connection)
    native = _native_module()
    if native is None:
        out('native_loaded=False game_build_supported=False hook_installed=False captures=0 last_error=module_unavailable')
        return
    try:
        native.initialize()
        status = native.status()
        out('native_loaded=%s game_build_supported=%s hook_installed=%s captures=%s last_error=%s' %
            (status.get('native_loaded'), status.get('game_build_supported'),
             status.get('hook_installed'), status.get('capture_count'),
             status.get('last_error')))
    except Exception as exc:
        out('native_loaded=False game_build_supported=False hook_installed=False captures=0 last_error=%s' % exc)


@sims4.commands.Command('kermp.native.take', command_type=sims4.commands.CommandType.Live)
def kermp_native_take(_connection=None):
    out = _out(_connection)
    native = _native_module()
    if native is None:
        out('native capture unavailable: module_unavailable')
        return
    try:
        capture = native.take_build_operation()
        if capture is None:
            out('native capture queue empty')
        else:
            out('capture_id=%s payload_size=%s payload_hash=%s thread_id=%s' %
                (capture.get('capture_id'), capture.get('payload_size'),
                 capture.get('payload_hash'), capture.get('thread_id')))
    except Exception as exc:
        out('native capture unavailable: %s' % exc)


@sims4.commands.Command('kermp.build.replay_last', command_type=sims4.commands.CommandType.Live)
def kermp_build_replay_last(_connection=None):
    ok = adapter.replay_last()
    _out(_connection)('KerMP remote build replay applied=%s' % ok)


@sims4.commands.Command('kermp.build.probe', command_type=sims4.commands.CommandType.Live)
def kermp_build_probe(_connection=None):
    result = probe_wall_contours()
    snapshot = result['snapshot']
    out = _out(_connection)
    out('KerMP probe phase=%s callable=%s type=%s signature=%s count=%s' %
        (result['phase'], snapshot.get('callable'), snapshot.get('type'),
         snapshot.get('signature'), len(snapshot.get('contours') or [])))
    if result['phase'] == 'baseline':
        out('Baseline stored. Draw one wall, then run kermp.build.probe again.')
    else:
        delta = result['delta']
        out('before=%s after=%s added=%s removed=%s changed=%s' %
            (delta['before_count'], delta['after_count'], len(delta['added']),
             len(delta['removed']), len(delta['changed'])))


@sims4.commands.Command('kermp.build.eventprobe', command_type=sims4.commands.CommandType.Live)
def kermp_build_eventprobe(_connection=None):
    event = wall_event_probe()
    out = _out(_connection)
    out('wall_callback_attribute_exists=%s type=%s callable=%s semantics=%s' %
        (event['attribute_exists'], event['type'], event['callable'],
         event['collection_semantics']))
    out('wall_callback_registered=%s wall_events_seen=%s' %
        (event['registered'], event['event_count']))
    out('last_wall_event=%s' % event['last_event'])
