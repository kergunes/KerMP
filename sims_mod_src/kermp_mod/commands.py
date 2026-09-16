import json

import sims4.commands
import services

from . import hooks
from .hooks import (bridge, probe_wall_contours, wall_event_probe, enumerate_sims,
                    enumerate_objects, enumerate_affordances, inspect_distributor_boundary)
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


@sims4.commands.Command('kermp.distributor.status', command_type=sims4.commands.CommandType.Live)
def kermp_distributor_status(_connection=None):
    result = inspect_distributor_boundary()
    out = _out(_connection)
    out('distributor_methods=%s omega_methods=%s error=%s' %
        (','.join(result.get('distributor') or []) or 'none',
         ','.join(result.get('client_omega') or []) or 'none', result.get('error') or 'none'))
    for name, signature in sorted((result.get('distributor_signatures') or {}).items()):
        out('distributor.%s signature=%s' % (name, signature))


@sims4.commands.Command('kermp.sims', command_type=sims4.commands.CommandType.Live)
def kermp_sims(_connection=None):
    sims = enumerate_sims()
    bridge.emit('sims.state', {'sims': sims})
    out = _out(_connection)
    for sim in sims:
        out('sim_id=%s name=%s controllers=%s' %
            (sim['sim_id'], sim['name'], ','.join(sim.get('controllers') or []) or 'none'))
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
