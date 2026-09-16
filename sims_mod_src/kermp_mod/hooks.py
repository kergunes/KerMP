"""KerMP game integration, Python 3.7 compatible.

Today this module proves script loading, sidecar connectivity, zone-load readiness,
and the transport contract. The Build/Buy native call remains the hard spike.
"""

import base64
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
_view_updates_sent = 0
_last_view_update_size = 0
_last_view_update_msg_id = None
_game_message_capture_installed = False
_MAX_RAW_GAME_MESSAGE_BYTES = 2 * 1024 * 1024
_travel_selected_sim_id = None
_travel_batch_complete = False
_travel_native_bypass = False
_travel_api_info = {}
_travel_zone_reported = False
_travel_local_zone_loaded = False
_travel_selected_sim_restored = False
_travel_last_error = None


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
    bridge.on('travel.view_batch', _travel_view_batch)
    bridge.on('travel.abort', _travel_abort)
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
    _install_game_message_capture()
    _install_travel_hook()
    _log('KerMP installed')


def _sidecar_welcome(payload):
    global _sidecar_role
    _sidecar_role = payload.get('role')
    _log('Sidecar connected role=%s' % payload.get('role'))


def _view_update_message_id():
    try:
        from protocolbuffers import Consts_pb2
        return int(getattr(Consts_pb2, 'MSG_OBJECTS_VIEW_UPDATE'))
    except Exception:
        return None


def _install_game_message_capture():
    """Observe host Client.send_message and forward native ViewUpdate bytes."""
    global _game_message_capture_installed
    if _game_message_capture_installed:
        return True
    try:
        from server.client import Client
        original = getattr(Client, 'send_message', None)
        if original is None:
            _log('KERMP DISTRIBUTOR CAPTURE unavailable reason=Client.send_message_missing')
            return False
        if getattr(original, '_kermp_wrapped', False):
            _game_message_capture_installed = True
            return True

        def wrapped(self, *args, **kwargs):
            global _view_updates_sent, _last_view_update_size, _last_view_update_msg_id
            result = original(self, *args, **kwargs)
            try:
                if _sidecar_role != 'host':
                    return result
                msg_id = kwargs.get('msg_id')
                if msg_id is None and args:
                    msg_id = args[0]
                expected = _view_update_message_id()
                if expected is None or int(msg_id) != expected:
                    return result
                message = kwargs.get('msg')
                if message is None:
                    message = kwargs.get('message')
                if message is None and len(args) > 1:
                    message = args[1]
                serializer = getattr(message, 'SerializeToString', None)
                if not callable(serializer):
                    _log('KERMP DISTRIBUTOR CAPTURE skipped reason=message_not_serializable')
                    return result
                raw = serializer()
                if not isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw)
                if len(raw) > _MAX_RAW_GAME_MESSAGE_BYTES:
                    _log('KERMP DISTRIBUTOR CAPTURE skipped reason=payload_too_large size=%s' % len(raw))
                    return result
                sent = bridge.emit('game.raw_message', {
                    'msg_id': int(msg_id),
                    'payload_b64': base64.b64encode(raw).decode('ascii'),
                })
                if sent:
                    _view_updates_sent += 1
                    _last_view_update_size = len(raw)
                    _last_view_update_msg_id = int(msg_id)
                    _log('KERMP DISTRIBUTOR CAPTURE msg_id=%s size=%s count=%s' %
                         (msg_id, len(raw), _view_updates_sent))
            except Exception:
                _log('KERMP DISTRIBUTOR CAPTURE ERROR %s' % traceback.format_exc())
            return result

        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        Client.send_message = wrapped
        _game_message_capture_installed = True
        _log('KERMP DISTRIBUTOR CAPTURE installed Client.send_message')
        return True
    except Exception:
        _log('KERMP DISTRIBUTOR CAPTURE install error %s' % traceback.format_exc())
        return False


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
    """Deliver one authoritative host game message into the local Sims client."""
    global _last_view_update_size, _last_view_update_msg_id
    if _sidecar_role != 'client':
        return False
    try:
        raw = base64.b64decode(str(payload.get('payload_b64', '')), validate=True)
        if len(raw) > _MAX_RAW_GAME_MESSAGE_BYTES:
            raise ValueError('payload_too_large')
        import services
        client = services.get_first_client()
        if client is None:
            raise ValueError('first_client_unavailable')
        omega = getattr(client, 'omega', None)
        if omega is None:
            raise ValueError('omega_unavailable')
        send = getattr(omega, 'send', None)
        if not callable(send):
            raise ValueError('omega_send_unavailable')
        msg_id = int(payload['msg_id'])
        send(msg_id, raw)
        _last_view_update_size = len(raw)
        _last_view_update_msg_id = msg_id
        _log('KERMP DISTRIBUTOR APPLY msg_id=%s size=%s' % (msg_id, len(raw)))
        return True
    except Exception as exc:
        _log('KERMP DISTRIBUTOR APPLY ERROR %s: %s' % (type(exc).__name__, exc))
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
        'omega_type': None, 'omega_repr': None, 'omega_send_callable': False,
        'capture_installed': _game_message_capture_installed,
        'consts': {}, 'error': None,
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
                result['omega_send_callable'] = callable(getattr(omega, 'send', None))
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
    global _pending_travel_txn, _travel_epoch, _travel_selected_sim_id, _travel_zone_reported
    _pending_travel_txn = payload.get('txn_id')
    _travel_epoch = int(payload.get('epoch', 0))
    _travel_selected_sim_id = _active_sim_id()
    _travel_zone_reported = False
    if _sidecar_role == 'client':
        begin_travel_buffer(_travel_epoch)
    # v0.0.1 is immediately ready after storing local state. Selected-Sim/camera
    # snapshots are added once the travel call itself is bound.
    bridge.emit('travel.ready', {'txn_id': _pending_travel_txn, 'epoch': _travel_epoch})


def _travel_commit(payload):
    global _pending_travel_txn, _travel_epoch, _travel_batch_complete, _travel_zone_reported, _travel_api_info, _travel_last_error
    _pending_travel_txn = payload.get('txn_id')
    _travel_epoch = int(payload.get('epoch', _travel_epoch))
    _travel_batch_complete = False
    _travel_zone_reported = False
    _travel_api_info = {'zone_id': str(payload.get('zone_id')), 'signature': 'unknown'}
    _travel_last_error = None
    if _sidecar_role == 'client':
        begin_travel_buffer(_travel_epoch)
    try:
        if _sidecar_role == 'host' or _sidecar_role == 'client':
            result = _invoke_native_travel(payload)
            _log('KERMP TRAVEL NATIVE INVOKED zone=%s api=%s result=%s' %
                 (payload.get('zone_id'), _travel_api_info.get('signature'), repr(result)[:200]))
    except Exception as exc:
        _travel_last_error = '%s: %s' % (type(exc).__name__, exc)
        _log('KERMP TRAVEL NATIVE ERROR %s: %s' % (type(exc).__name__, exc))
        bridge.emit('travel.abort', {'txn_id': _pending_travel_txn, 'epoch': _travel_epoch,
                                     'reason': '%s: %s' % (type(exc).__name__, exc)})


def _travel_view_batch(payload):
    global _travel_batch_complete
    if int(payload.get('epoch', -1)) != _travel_epoch:
        return
    kind = str(payload.get('kind', '')).lower()
    if kind == 'begin':
        _travel_batch_complete = False
        if _sidecar_role == 'client':
            begin_travel_buffer(_travel_epoch)
    elif kind == 'end':
        _travel_batch_complete = True
        if _sidecar_role == 'client' and _zone_is_loaded(payload.get('zone_id')):
            _finish_zone_hydration()


def _travel_abort(payload):
    global _pending_travel_txn, _travel_last_error
    _travel_last_error = str(payload.get('reason') or 'travel_aborted')
    _pending_travel_txn = None


def _active_sim_id():
    try:
        import services
        client = services.get_first_client()
        info = getattr(client, 'active_sim_info', None) if client else None
        return str(getattr(info, 'id', '')) if info else None
    except Exception:
        return None


def _zone_is_loaded(zone_id=None):
    try:
        import services
        current = str(services.current_zone_id())
        return not zone_id or current == str(zone_id)
    except Exception:
        return False


def _restore_active_sim():
    if not _travel_selected_sim_id:
        return False
    try:
        import services
        client = services.get_first_client()
        setter = getattr(client, 'set_active_sim_by_id', None) if client else None
        if callable(setter):
            setter(int(_travel_selected_sim_id))
            return True
        info = services.sim_info_manager().get(int(_travel_selected_sim_id))
        sim = info.get_sim_instance(allow_hidden_flags=True) if info else None
        if sim is not None and client is not None:
            setter = getattr(client, 'set_active_sim', None)
            if callable(setter):
                setter(sim)
                return True
    except Exception as exc:
        _log('KERMP selected Sim restore deferred/error: %s' % exc)
    return False


def _finish_zone_hydration():
    global _travel_zone_reported, _travel_selected_sim_restored
    if _travel_zone_reported:
        return True
    restored = _restore_active_sim()
    _travel_selected_sim_restored = restored
    bridge.emit('travel.zone_ready', {'txn_id': _pending_travel_txn, 'epoch': _travel_epoch,
                                      'zone_id': str(_current_zone_id()), 'selected_sim_restored': restored})
    _travel_zone_reported = True
    return True


def _current_zone_id():
    try:
        import services
        return services.current_zone_id()
    except Exception:
        return 0


def _invoke_native_travel(payload):
    """Call the installed build's travel command using discovered parameter names."""
    global _travel_api_info, _travel_native_bypass
    import inspect
    import world.travel_commands as travel_commands
    fn = getattr(travel_commands, 'travel_sims_to_zone', None)
    if not callable(fn):
        raise RuntimeError('travel_sims_to_zone_not_found')
    _travel_api_info = {'module': 'world.travel_commands', 'signature': 'uninspectable'}
    try:
        _travel_api_info['signature'] = str(inspect.signature(fn))
    except Exception:
        pass
    zone_id = int(str(payload.get('zone_id')))
    actor_ids = [int(str(x)) for x in payload.get('actor_ids') or []]
    if not actor_ids:
        try:
            import services
            active = getattr(services.get_first_client(), 'active_sim_info', None)
            if active:
                actor_ids = [int(active.id)]
        except Exception:
            pass
    try:
        import services
        persistence = services.get_persistence_service()
        if persistence is not None and hasattr(persistence, 'get_zone_proto_buff'):
            if persistence.get_zone_proto_buff(zone_id) is None:
                raise ValueError('destination_zone_not_found')
    except AttributeError:
        pass
    if not actor_ids:
        raise ValueError('travel_actor_not_found')
    sig = inspect.signature(fn)
    kwargs = {}
    for name, param in sig.parameters.items():
        lower = name.lower()
        if 'zone' in lower and ('id' in lower or lower == 'zone'):
            kwargs[name] = zone_id
        elif 'sim' in lower and ('id' in lower or 'ids' in lower):
            kwargs[name] = actor_ids if lower.endswith('ids') or 'ids' in lower else actor_ids[0]
    missing = [n for n, p in sig.parameters.items()
               if p.default is inspect.Parameter.empty and n not in kwargs and
               n not in ('self', 'connection', '_connection')]
    if missing:
        raise RuntimeError('travel_signature_requires_unmapped=%s signature=%s' % (missing, sig))
    _travel_native_bypass = True
    try:
        return fn(**kwargs)
    finally:
        _travel_native_bypass = False


def _install_travel_hook():
    """Intercept the final native travel command and route it through KerMP."""
    try:
        import inspect
        import world.travel_commands as travel_commands
        original = getattr(travel_commands, 'travel_sims_to_zone', None)
        if not callable(original) or getattr(original, '_kermp_wrapped', False):
            return False

        def wrapped(*args, **kwargs):
            if _travel_native_bypass:
                return original(*args, **kwargs)
            try:
                bound = inspect.signature(original).bind_partial(*args, **kwargs)
                values = bound.arguments
            except Exception:
                values = kwargs
            zone = None
            actors = []
            for name, value in values.items():
                lower = name.lower()
                if 'zone' in lower and ('id' in lower or lower == 'zone'):
                    zone = value
                elif 'sim' in lower and ('id' in lower or 'ids' in lower):
                    actors = list(value) if isinstance(value, (list, tuple, set)) else [value]
            if zone is None:
                raise RuntimeError('natural_travel_zone_id_unresolved')
            payload = {'zone_id': str(getattr(zone, 'zone_id', zone)),
                       'actor_ids': [str(getattr(x, 'id', x)) for x in actors]}
            _log('KERMP NATURAL TRAVEL INTERCEPT zone=%s actors=%s' %
                 (payload['zone_id'], payload['actor_ids']))
            bridge.emit('travel.request', payload)
            return None

        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        travel_commands.travel_sims_to_zone = wrapped
        _log('KERMP natural travel hook installed signature=%s' % inspect.signature(original))
        return True
    except Exception as exc:
        _log('KERMP natural travel hook unavailable: %s' % exc)
        return False


def _travel_resume(payload):
    if _sidecar_role == 'client' and int(payload.get('epoch', _travel_epoch)) == _travel_epoch:
        if not _travel_batch_complete:
            _log('KERMP travel resume received before authoritative batch END')
        elif _zone_is_loaded(payload.get('zone_id')):
            _finish_zone_hydration()
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
    global _pending_travel_txn, _travel_batch_complete, _travel_local_zone_loaded
    if not _pending_travel_txn:
        return
    try:
        import services
        zone_id = services.current_zone_id()
    except Exception:
        zone_id = 0
    _travel_local_zone_loaded = True
    if _sidecar_role == 'client':
        # Local loading is necessary but not sufficient: wait for host END.
        if _travel_batch_complete:
            _finish_zone_hydration()
        return
    _travel_batch_complete = True
    bridge.emit('travel.view_batch', {'txn_id': _pending_travel_txn, 'epoch': _travel_epoch,
                                      'kind': 'end', 'zone_id': str(zone_id)})
    bridge.emit('travel.zone_ready', {'txn_id': _pending_travel_txn, 'epoch': _travel_epoch,
                                      'zone_id': str(zone_id)})
