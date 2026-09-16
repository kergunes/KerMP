import json

import sims4.commands
import services

from .hooks import bridge, probe_wall_contours
from .build_adapter import adapter, build_buy_surface


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
