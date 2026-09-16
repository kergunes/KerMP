"""KerMP game integration, Python 3.7 compatible.

Today this module proves script loading, sidecar connectivity, zone-load readiness,
and the transport contract. The Build/Buy native call remains the hard spike.
"""

import traceback

from .bridge_client import KerMPBridgeClient
from .build_adapter import adapter

bridge = KerMPBridgeClient()
_installed = False
_pending_travel_txn = None
_wall_callback_registered = False
_wall_event_count = 0
_last_wall_event = None
_wall_callback_info = {}
_last_sims = []
_sidecar_role = None
_travel_epoch = 0
_travel_buffering = False
_travel_buffer = []
_MAX_TRAVEL_BUFFER = 256
_view_updates_received = 0


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
    bridge.on('sim.enumerate', _sim_enumerate)
    bridge.on('sim.select', _sim_select)
    bridge.on('interaction.request', _interaction_request)
    bridge.on('game.raw_message', _raw_game_message)
    bridge.start()
    _install_build_buy_hooks()
    _install_wall_contour_callback()
    _install_zone_hooks()
    _log('KerMP installed')


def _sidecar_welcome(payload):
    global _sidecar_role
    _sidecar_role = payload.get('role')
    _log('Sidecar connected role=%s' % payload.get('role'))


def _raw_game_message(payload):
    if _sidecar_role != 'client':
        _log('KERMP RAW MESSAGE IGNORED role=%s' % _sidecar_role)
        return
    if receive_raw_game_message(payload):
        _log('KERMP RAW MESSAGE RECEIVED msg_id=%s size=%s' %
             (payload.get('msg_id'), len(str(payload.get('payload_b64', '')))))


def enumerate_sims():
    """Return live SimInfo records; IDs stay strings on the wire."""
    global _last_sims
    result = []
    try:
        import services
        manager = services.sim_info_manager()
        for info in manager.get_all():
            sim_id = getattr(info, 'id', None)
            if sim_id is None:
                continue
            instance = info.get_sim_instance(allow_hidden_flags=True)
            if instance is None:
                continue
            name = getattr(info, 'full_name', None) or ('%s %s' %
                    (getattr(info, 'first_name', ''), getattr(info, 'last_name', ''))).strip()
            result.append({'sim_id': str(sim_id), 'name': name or str(sim_id), 'controllers': []})
    except Exception as exc:
        _log('KERMP SIM ENUM ERROR %s: %s' % (type(exc).__name__, exc))
    _last_sims = result
    return result


def enumerate_objects(limit=40):
    """Bounded, current-zone object diagnostics; IDs come from the live manager."""
    result = []
    try:
        import services
        manager = services.object_manager()
        for obj in manager.get_all():
            if len(result) >= int(limit):
                break
            obj_id = getattr(obj, 'id', None)
            if obj_id is None:
                continue
            name = getattr(obj, 'definition', None)
            name = getattr(name, 'name', None) or getattr(obj, 'full_name', None) or type(obj).__name__
            result.append({'object_id': str(obj_id), 'name': str(name), 'type': type(obj).__name__})
    except Exception as exc:
        _log('KERMP OBJECT ENUM ERROR %s: %s' % (type(exc).__name__, exc))
    return result


def _object(object_id):
    import services
    obj = services.object_manager().get(int(str(object_id)))
    if obj is None:
        raise ValueError('object_not_found')
    return obj


def enumerate_affordances(object_id, sim_id=None, limit=40):
    """Inspect affordance collections exposed by this installed build."""
    obj = _object(object_id)
    candidates = []
    sources = (
        obj,
        getattr(obj, 'definition', None),
        type(obj),
    )
    for source in sources:
        if source is None:
            continue
        for attr in ('_super_affordances', 'super_affordances', 'affordances', 'available_affordances'):
            value = getattr(source, attr, None)
            if value is None:
                continue
            try:
                if callable(value):
                    value = value()
                values = list(value)
            except Exception:
                continue
            if values:
                candidates = values
                break
        if candidates:
            break
    result = []
    seen = set()
    for affordance in candidates:
        aid = getattr(affordance, 'guid64', None) or getattr(affordance, 'guid', None) or getattr(affordance, 'id', None)
        if aid is None:
            continue
        aid = str(aid)
        if aid in seen:
            continue
        seen.add(aid)
        name = getattr(affordance, '__name__', None) or getattr(affordance, 'display_name', None) or type(affordance).__name__
        result.append({'affordance_id': aid, 'name': str(name)})
        if len(result) >= int(limit):
            break
    return result


def begin_travel_buffer(epoch):
    global _travel_epoch, _travel_buffering, _travel_buffer
    _travel_epoch = int(epoch)
    _travel_buffering = True
    _travel_buffer = []


def receive_raw_game_message(payload):
    global _travel_buffer, _view_updates_received
    if _travel_buffering:
        if len(_travel_buffer) >= _MAX_TRAVEL_BUFFER:
            _log('KERMP VIEW UPDATE BUFFER FULL epoch=%s' % _travel_epoch)
            return False
        if int(payload.get('epoch', _travel_epoch)) != _travel_epoch:
            _log('KERMP STALE VIEW UPDATE epoch=%s expected=%s' % (payload.get('epoch'), _travel_epoch))
            return False
        _travel_buffer.append(payload)
        return True
    applied = _apply_raw_game_message(payload)
    if applied:
        _view_updates_received += 1
    return applied


def _apply_raw_game_message(payload):
    # The client-side Distributor application is build-specific and is installed by
    # the optional capture/apply hook below once its exact API is observed.
    return False


def flush_travel_buffer():
    global _travel_buffering, _travel_buffer, _view_updates_received
    queued = list(_travel_buffer)
    _travel_buffer = []
    _travel_buffering = False
    for payload in queued:
        if _apply_raw_game_message(payload):
            _view_updates_received += 1
    return len(queued)


def inspect_distributor_boundary():
    """Report live Distributor/client surfaces from the installed build."""
    result = {
        'modules': [], 'distributor': [], 'distributor_instance': [],
        'client_methods': [], 'client_omega': [], 'client_type': None,
        'omega_type': None, 'omega_repr': None, 'consts': {}, 'error': None,
    }

    def interesting(names):
        needles = ('op', 'message', 'view', 'send', 'process', 'flush', 'distribut')
        return sorted(name for name in names
                      if not name.startswith('__') and any(n in name.lower() for n in needles))[:60]

    try:
        import inspect
        from distributor import system as distributor_system
        result['modules'].append('distributor.system')
        cls = getattr(distributor_system, 'Distributor', None)
        if cls is not None:
            result['distributor'] = interesting(dir(cls))
            result['distributor_signatures'] = {}
            for name in result['distributor']:
                member = getattr(cls, name, None)
                if callable(member):
                    try:
                        result['distributor_signatures'][name] = str(inspect.signature(member))[:200]
                    except Exception:
                        result['distributor_signatures'][name] = 'uninspectable'
            try:
                instance = cls.instance()
            except Exception:
                instance = None
            if instance is not None:
                result['distributor_instance'] = interesting(dir(instance))
                result['distributor_instance_type'] = type(instance).__name__
    except Exception as exc:
        result['error'] = '%s: %s' % (type(exc).__name__, exc)

    try:
        from protocolbuffers import Consts_pb2
        for name in dir(Consts_pb2):
            if 'OBJECTS_VIEW_UPDATE' in name or 'VIEW_UPDATE' in name:
                try:
                    result['consts'][name] = int(getattr(Consts_pb2, name))
                except Exception:
                    result['consts'][name] = repr(getattr(Consts_pb2, name))[:200]
    except Exception as exc:
        result['consts_error'] = '%s: %s' % (type(exc).__name__, exc)

    try:
        import services
        client = services.get_first_client()
        if client is not None:
            result['client_type'] = type(client).__name__
            result['client_methods'] = interesting(dir(client))
            omega = getattr(client, 'omega', None)
            if omega is not None:
                result['omega_type'] = type(omega).__name__
                result['omega_repr'] = repr(omega)[:300]
                public = sorted(name for name in dir(omega) if not name.startswith('__'))
                result['client_omega'] = interesting(public)
                if not result['client_omega']:
                    result['client_omega'] = public[:60]
    except Exception as exc:
        result['omega_error'] = '%s: %s' % (type(exc).__name__, exc)
    return result


def _sim_enumerate(_payload):
    bridge.emit('sims.state', {'sims': enumerate_sims()})


def _sim_select(payload):
    # Selection is authoritative in the sidecar. This event is intentionally
    # diagnostic only; the host owns player -> Sim and the game owns execution.
    _log('KERMP SIM SELECT player=%s sim=%s' % (payload.get('player_id'), payload.get('sim_id')))


def _resolve_interaction(payload):
    import services
    sim_id = int(str(payload['sim_id']))
    info = services.sim_info_manager().get(sim_id)
    sim = info.get_sim_instance(allow_hidden_flags=True) if info else None
    if sim is None:
        raise ValueError('sim_not_loaded')
    target_id = payload.get('target_id')
    target = None
    if target_id not in (None, '', 0, '0'):
        target = services.object_manager().get(int(str(target_id)))
        if target is None:
            raise ValueError('target_not_found')
    affordance_id = int(str(payload['affordance_id']))
    from sims4.resources import Types
    affordance = services.get_instance_manager(Types.INTERACTION).get(affordance_id)
    if affordance is None:
        raise ValueError('affordance_not_found')
    from interactions.context import InteractionContext, InteractionSource
    from interactions.priority import Priority
    context = InteractionContext(sim, InteractionSource.SCRIPT, Priority.High)
    return sim, affordance, target, context


def _interaction_request(payload):
    request_id = str(payload.get('request_id') or '')
    try:
        sim, affordance, target, context = _resolve_interaction(payload)
        result = sim.push_super_affordance(affordance, target, context)
        if not result:
            raise ValueError('push_rejected')
        bridge.emit('interaction.started', {'request_id': request_id, 'sim_id': payload.get('sim_id'),
                                            'interaction_id': str(getattr(result, 'id', ''))})
        _log('KERMP INTERACTION STARTED request=%s sim=%s affordance=%s target=%s' %
             (request_id, payload.get('sim_id'), payload.get('affordance_id'), payload.get('target_id')))
    except Exception as exc:
        bridge.emit('interaction.rejected', {'request_id': request_id, 'reason': '%s: %s' % (type(exc).__name__, exc)})
        _log('KERMP INTERACTION REJECTED request=%s error=%s' % (request_id, exc))


def _travel_prepare(payload):
    global _pending_travel_txn
    _pending_travel_txn = payload.get('txn_id')
    if _sidecar_role == 'client':
        begin_travel_buffer(payload.get('epoch', 0))
    # v0.0.1 is immediately ready after storing local state. Selected-Sim/camera
    # snapshots are added once the travel call itself is bound.
    bridge.emit('travel.ready', {'txn_id': _pending_travel_txn})


def _travel_commit(payload):
    global _pending_travel_txn
    _pending_travel_txn = payload.get('txn_id')
    if _sidecar_role == 'client':
        begin_travel_buffer(payload.get('epoch', 0))
    # TODO HARD SPIKE: call the game's travel service for payload['zone_id'].
    # Do not fake zone_ready here: the zone-load hook below must emit it only
    # after the destination has actually loaded.
    _log('Travel commit received for zone=%s' % payload.get('zone_id'))


def _travel_resume(payload):
    if _sidecar_role == 'client':
        flush_travel_buffer()
    _log('Travel barrier complete txn=%s' % payload.get('txn_id'))


def _build_apply(payload):
    try:
        applied = adapter.apply_remote(payload)
        _log('KERMP BUILD APPLY seq=%s op=%s applied=%s suppression=%s' %
             (payload.get('op_seq'), payload.get('op'), applied,
              adapter.applying_remote))
    except Exception:
        _log('KERMP BUILD APPLY ERROR %s' % traceback.format_exc())


def capture_build_operation(payload):
    """Entry point for a future tested UI/native hook.

    A hook must call this only once the operation is committed.  Returning the
    normalized operation keeps the game boundary independent from LAN details.
    """
    try:
        operation = adapter.capture_local(payload)
    except Exception:
        _log('KERMP BUILD CAPTURE ERROR %s' % traceback.format_exc())
        return False
    if not operation:
        return False
    _log('KERMP BUILD CAPTURE type=%s data=%s' %
         (operation['op'], operation['data']))
    bridge.emit('build.operation', operation)
    return True


def probe_wall_contours():
    result = adapter.probe()
    snapshot = result['snapshot']
    _log('KERMP BUILD PROBE phase=%s module=%s callable=%s type=%s signature=%s raw_type=%s raw=%s error=%s' %
         (result['phase'], snapshot.get('module_available'), snapshot.get('callable'),
          snapshot.get('type'), snapshot.get('signature'), snapshot.get('raw_type'),
          snapshot.get('raw_repr'), snapshot.get('error')))
    if result['phase'] == 'delta':
        delta = result['delta']
        _log('KERMP BUILD PROBE DELTA before=%s after=%s added=%s removed=%s changed=%s' %
             (delta['before_count'], delta['after_count'], delta['added'],
              delta['removed'], delta['changed']))
    return result


def _on_build_buy_enter():
    _log('KERMP BUILD MODE ENTER')
    bridge.emit('build.lock_request', {})


def _on_build_buy_exit():
    _log('KERMP BUILD MODE EXIT')
    bridge.emit('build.lock_release', {})


def _wall_contour_update_callback(*args, **kwargs):
    """Observe the game's real wall-change notification, if exposed."""
    global _wall_event_count, _last_wall_event
    _wall_event_count += 1
    _last_wall_event = {'args': [repr(value)[:1000] for value in args],
                        'kwargs': {str(key): repr(value)[:1000]
                                   for key, value in kwargs.items()}}
    _log('KERMP WALL EVENT count=%s args=%s kwargs=%s' %
         (_wall_event_count, _last_wall_event['args'],
          _last_wall_event['kwargs']))


def inspect_wall_contour_callback():
    """Inspect current_zone().wall_contour_update_callbacks safely."""
    global _wall_callback_info
    result = {'zone_available': False, 'attribute_exists': False,
              'type': None, 'repr': None, 'callable': False,
              'collection_semantics': None, 'registration_method': None,
              'registered': _wall_callback_registered,
              'event_count': _wall_event_count,
              'last_event': _last_wall_event, 'error': None}
    try:
        import services
        zone = services.current_zone()
        result['zone_available'] = zone is not None
        if zone is None:
            _wall_callback_info = result
            return result
        result['attribute_exists'] = hasattr(zone, 'wall_contour_update_callbacks')
        if not result['attribute_exists']:
            _wall_callback_info = result
            return result
        callbacks = getattr(zone, 'wall_contour_update_callbacks')
        result['type'] = type(callbacks).__name__
        result['repr'] = repr(callbacks)[:1000]
        result['callable'] = bool(callable(callbacks))
        if hasattr(callbacks, 'append') and callable(getattr(callbacks, 'append')):
            result['collection_semantics'] = 'appendable'
        elif hasattr(callbacks, 'register') and callable(getattr(callbacks, 'register')):
            result['collection_semantics'] = 'registerable'
        else:
            result['collection_semantics'] = 'callable-only' if result['callable'] else 'opaque'
    except Exception as exc:
        result['error'] = '%s: %s' % (type(exc).__name__, exc)
    _wall_callback_info = result
    return result


def _install_wall_contour_callback():
    """Register only through an explicitly collection-like Zone callback list."""
    global _wall_callback_registered
    result = inspect_wall_contour_callback()
    if _wall_callback_registered or not result.get('attribute_exists'):
        return result
    try:
        import services
        callbacks = getattr(services.current_zone(), 'wall_contour_update_callbacks')
        method_name = None
        method = getattr(callbacks, 'append', None)
        if callable(method):
            method_name = 'append'
        else:
            method = getattr(callbacks, 'register', None)
            if callable(method):
                method_name = 'register'
        if method is None:
            return result
        method(_wall_contour_update_callback)
        _wall_callback_registered = True
        result['registration_method'] = method_name
        result['registered'] = True
        _log('KERMP wall contour callback registered type=%s method=%s' %
             (result.get('type'), method_name))
    except Exception as exc:
        result['error'] = '%s: %s' % (type(exc).__name__, exc)
        _log('KERMP wall contour callback unavailable error=%s' % result['error'])
    result['event_count'] = _wall_event_count
    _wall_callback_info = result
    return result


def wall_event_probe():
    """Refresh callback registration and return concise live diagnostics."""
    result = _install_wall_contour_callback()
    result['registered'] = _wall_callback_registered
    result['event_count'] = _wall_event_count
    result['last_event'] = _last_wall_event
    return result


def _install_build_buy_hooks():
    """Use the verified Build/Buy lifecycle callbacks for lease fallback.

    These callbacks are not wall-operation capture: the installed game exposes
    them as zero-argument enter/exit notifications only.
    """
    try:
        import build_buy
        register_enter = getattr(build_buy, 'register_build_buy_enter_callback')
        register_exit = getattr(build_buy, 'register_build_buy_exit_callback')
        register_enter(_on_build_buy_enter)
        register_exit(_on_build_buy_exit)
        _log('KERMP Build/Buy lifecycle hooks installed')
    except Exception:
        _log('KERMP Build/Buy lifecycle hooks unavailable')


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
