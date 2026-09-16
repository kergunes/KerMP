"""KerMP game integration, Python 3.7 compatible.

Today this module proves script loading, sidecar connectivity, zone-load readiness,
and the transport contract. The Build/Buy native call remains the hard spike.
"""

import traceback

from .bridge_client import KerMPBridgeClient

bridge = KerMPBridgeClient()
_installed = False
_pending_travel_txn = None


def _log(message):
    try:
        import sims4.log
        logger = sims4.log.Logger('KerMP')
        logger.info(message)
    except Exception:
        pass


def install():
    global _installed
    if _installed:
        return
    _installed = True
    bridge.on('travel.prepare', _travel_prepare)
    bridge.on('travel.commit', _travel_commit)
    bridge.on('travel.resume', _travel_resume)
    bridge.on('build.apply', _build_apply)
    bridge.on('sidecar.welcome', _sidecar_welcome)
    bridge.start()
    _install_zone_hooks()
    _log('KerMP installed')


def _sidecar_welcome(payload):
    _log('Sidecar connected role=%s' % payload.get('role'))


def _travel_prepare(payload):
    global _pending_travel_txn
    _pending_travel_txn = payload.get('txn_id')
    # v0.0.1 is immediately ready after storing local state. Selected-Sim/camera
    # snapshots are added once the travel call itself is bound.
    bridge.emit('travel.ready', {'txn_id': _pending_travel_txn})


def _travel_commit(payload):
    global _pending_travel_txn
    _pending_travel_txn = payload.get('txn_id')
    # TODO HARD SPIKE: call the game's travel service for payload['zone_id'].
    # Do not fake zone_ready here: the zone-load hook below must emit it only
    # after the destination has actually loaded.
    _log('Travel commit received for zone=%s' % payload.get('zone_id'))


def _travel_resume(payload):
    _log('Travel barrier complete txn=%s' % payload.get('txn_id'))


def _build_apply(payload):
    # TODO HARD SPIKE: invoke native Build/Buy operation. Keeping this handler
    # in one place lets us swap native bridge strategies without changing LAN.
    _log('Build apply seq=%s op=%s' % (payload.get('op_seq'), payload.get('op')))


def _install_zone_hooks():
    try:
        from zone import Zone
    except Exception:
        return
    original = getattr(Zone, 'do_zone_spin_up', None)
    if original is None or getattr(original, '_kermp_wrapped', False):
        return

    def wrapped(self, household_id, active_sim_id, *args, **kwargs):
        result = original(self, household_id, active_sim_id, *args, **kwargs)
        try:
            _after_zone_spin_up()
        except Exception:
            _log(traceback.format_exc())
        return result

    wrapped._kermp_wrapped = True
    Zone.do_zone_spin_up = wrapped


def _after_zone_spin_up():
    global _pending_travel_txn
    if not _pending_travel_txn:
        return
    try:
        import services
        zone_id = services.current_zone_id()
    except Exception:
        zone_id = 0
    bridge.emit('travel.zone_ready', {'txn_id': _pending_travel_txn, 'zone_id': str(zone_id)})
    _pending_travel_txn = None
