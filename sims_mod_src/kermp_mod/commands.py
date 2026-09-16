import json

import sims4.commands
import services

from .hooks import bridge


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
