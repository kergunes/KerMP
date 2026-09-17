"""KerMP game integration, Python 3.7 compatible.

Today this module proves script loading, sidecar connectivity, zone-load readiness,
and the transport contract. The Build/Buy native call remains the hard spike.
"""

import base64
import functools
import traceback

from .bridge_client import KerMPBridgeClient
from .build_adapter import adapter, bind_call, resolve_parent_context

bridge = KerMPBridgeClient()
_installed = False
_pending_travel_txn = None
_wall_callback_registered = False
_wall_callback_registration = None
_wall_event_count = 0
_last_wall_event = None
_wall_callback_info = {}
_last_sims = []
_sidecar_role = None
_sidecar_player_id = None
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
_build_hook_info = {}
_build_last_error = None
_build_capture_depth = 0
_simulation_status = {'role': 'unknown', 'timeline_suppression_available': False,
                      'installed': False, 'local_simulation_enabled': True,
                      'clock_source': 'local', 'bypass': False,
                      'last_error': 'sidecar_not_connected'}
_simulation_bypass = False
_timeline_suppression_installed = False
_patches = []
_message_stats = {'observed': 0, 'replicated': 0, 'dropped_local': 0,
                  'dropped_oversize': 0, 'dropped_unserializable': 0,
                  'dropped_unknown': 0, 'dropped_local_ops': 0, 'by_id': {}, 'last_msg_id': None,
                  'last_error': None}
_local_only_ids = None
_local_only_op_ids = None
_interaction_interception_installed = False
_active_sim_hook_installed = False
_last_published_active_sim_id = None
_authoritative_sim_id = None
_authoritative_controllers = {}
_local_active_sim_id = None
_interaction_request_by_id = {}
_cancel_interception_installed = False
_clock_hooks_installed = False
_clock_apply_bypass = False
_clock_status = {'installed': False, 'requests': 0, 'applied': 0, 'last_error': None}
_interaction_stats = {'forwarded': 0, 'dropped': 0, 'last_affordance_id': None,
                      'last_target_id': None, 'last_sim_id': None, 'last_error': None,
                      'sent': 0, 'accepted': 0, 'rejected': 0, 'started': 0,
                      'delivered': 0, 'apply_accepted': 0, 'apply_rejected': 0,
                      'last_request_id': None}


def _record_patch(target, attr, original):
    _patches.append((target, attr, original))


def _log(message):
    try:
        import sims4.log
        logger = sims4.log.Logger('KerMP')
        logger.info(message)
    except Exception:
        pass


def _safe_signature(value):
    try:
        import inspect
        return str(inspect.signature(value))
    except Exception as exc:
        return '<unavailable:%s>' % type(exc).__name__


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
    bridge.on('sim.selection_state', _sim_selection_state)
    bridge.on('sim.state', _sim_state)
    bridge.on('interaction.accepted', _interaction_accepted)
    bridge.on('interaction.rejected', _interaction_rejected)
    bridge.on('interaction.started', _interaction_started)
    bridge.on('interaction.finished', _interaction_finished)
    bridge.on('interaction.cancel', _interaction_cancel)
    bridge.on('interaction.request', _interaction_request)
    bridge.on('game.raw_message', _raw_game_message)
    bridge.on('clock.state', _clock_state)
    bridge.start()
    _install_build_buy_hooks()
    _install_object_build_hooks()
    _install_wall_contour_callback()
    _install_zone_hooks()
    _install_game_message_capture()
    _install_travel_hook()
    _install_interaction_interception()
    _install_active_sim_hooks()
    _install_cancel_interception()
    _install_clock_hooks()
    _log('KerMP installed')


def teardown():
    """Restore patched originals and stop the bridge before a hot reload.

    Returns a list of error strings; an empty list means teardown completed.
    The caller should abort the reload on a non-empty result, because the
    runtime may otherwise be left in an unknown half-dismantled state.
    """
    global _installed, _wall_callback_registered, _game_message_capture_installed, _timeline_suppression_installed, _interaction_interception_installed, _active_sim_hook_installed, _last_published_active_sim_id, _cancel_interception_installed, _clock_hooks_installed, _clock_apply_bypass
    errors = []
    # Stop accepting new bridge events first.
    try:
        bridge.stop()
    except Exception as exc:
        errors.append('bridge stop: %s' % exc)
    for target, attr, original in _patches:
        try:
            setattr(target, attr, original)
        except Exception as exc:
            errors.append('restore %s.%s: %s' % (type(target).__name__, attr, exc))
    del _patches[:]
    try:
        _teardown_wall_contour_callback()
    except Exception as exc:
        errors.append('wall callback: %s' % exc)
    try:
        _teardown_build_buy_callbacks()
    except Exception as exc:
        errors.append('build/buy callbacks: %s' % exc)
    _wall_callback_registered = False
    _game_message_capture_installed = False
    _timeline_suppression_installed = False
    _interaction_interception_installed = False
    _active_sim_hook_installed = False
    _cancel_interception_installed = False
    _clock_hooks_installed = False
    _clock_apply_bypass = False
    _last_published_active_sim_id = None
    _installed = False
    return errors


def _teardown_build_buy_callbacks():
    """Best-effort unregister of Build/Buy lifecycle callbacks."""
    try:
        import build_buy
    except Exception:
        return
    for unregister_name, callback in (
            ('unregister_build_buy_enter_callback', _on_build_buy_enter),
            ('unregister_build_buy_exit_callback', _on_build_buy_exit)):
        unregister = getattr(build_buy, unregister_name, None)
        if callable(unregister):
            try:
                unregister(callback)
            except Exception as exc:
                _log('KERMP TEARDOWN build/buy unregister failed %s: %s' %
                     (unregister_name, exc))


def _teardown_wall_contour_callback():
    """Remove the wall contour callback using its recorded collection semantics."""
    global _wall_callback_registered, _wall_callback_registration
    registration, _wall_callback_registration = _wall_callback_registration, None
    _wall_callback_registered = False
    if not registration:
        return
    collection, method_name = registration
    method_names = ('unregister', 'remove', 'discard') if method_name == 'register' else ('remove', 'discard')
    for name in method_names:
        method = getattr(collection, name, None)
        if not callable(method):
            continue
        try:
            method(_wall_contour_update_callback)
            return
        except Exception:
            continue


def _sidecar_welcome(payload):
    global _sidecar_role, _sidecar_player_id
    _sidecar_role = payload.get('role')
    _sidecar_player_id = payload.get('player_id')
    _configure_simulation_authority(_sidecar_role)
    _install_active_sim_hooks()
    _install_clock_hooks()
    try:
        _publish_active_sim_if_ready()
    except Exception:
        _log(traceback.format_exc())
    _log('Sidecar connected role=%s' % payload.get('role'))


def _clock_speed_value(value):
    try:
        return int(getattr(value, 'value', value))
    except (TypeError, ValueError):
        name = str(getattr(value, 'name', value)).upper()
        return {'PAUSED': 0, 'NORMAL': 1, 'SPEED2': 2, 'SPEED3': 3}.get(name, 1)


def _clock_request(speed=None, paused=None):
    global _clock_status
    _clock_status['requests'] += 1
    payload = {}
    if speed is not None:
        payload['speed'] = max(0, min(3, _clock_speed_value(speed)))
        bridge.emit('clock.request_speed', payload)
    else:
        payload['paused'] = bool(paused)
        bridge.emit('clock.request_pause', payload)


def _clock_state(payload):
    """Apply the host's clock decision to this Sims process."""
    global _clock_apply_bypass
    state = dict(payload or {})
    speed = _clock_speed_value(state.get('speed', 0 if state.get('paused') else 1))
    speed = 0 if bool(state.get('paused')) else max(1, min(3, speed))
    try:
        import services
        service = services.game_clock_service()
        import clock
        mode = getattr(getattr(clock, 'ClockSpeedMode', None),
                       {0: 'PAUSED', 1: 'NORMAL', 2: 'SPEED2', 3: 'SPEED3'}[speed])
        source = getattr(getattr(clock, 'GameSpeedChangeSource', None), 'GAMEPLAY', None)
        _clock_apply_bypass = True
        setter = getattr(service, 'set_clock_speed')
        try:
            setter(mode, source=source, reason='KerMP authoritative clock', immediate=True)
        except TypeError:
            setter(mode)
        _clock_status['applied'] += 1
        _clock_status['last_error'] = None
    except Exception as exc:
        _clock_status['last_error'] = '%s: %s' % (type(exc).__name__, exc)
        _log('KERMP CLOCK APPLY ERROR %s' % _clock_status['last_error'])
    finally:
        _clock_apply_bypass = False


def _install_clock_hooks():
    """Route every UI/game clock change through the host and replay its result."""
    global _clock_hooks_installed
    if _clock_hooks_installed:
        return True
    try:
        import clock
        game_clock = getattr(clock, 'GameClock', None)
        if game_clock is None:
            _clock_status['last_error'] = 'game_clock_missing'
            return False
        for name in ('set_clock_speed', 'push_speed', 'pop_speed'):
            original = getattr(game_clock, name, None)
            if not callable(original) or getattr(original, '_kermp_wrapped', False):
                continue
            if name == 'set_clock_speed':
                def wrapped(self, speed, *args, **kwargs):
                    if _clock_apply_bypass:
                        return original_set(self, speed, *args, **kwargs)
                    if _sidecar_role == 'client':
                        _clock_request(speed=speed)
                        return None
                    result = original_set(self, speed, *args, **kwargs)
                    bridge.emit('clock.state', {'speed': _clock_speed_value(speed), 'paused': _clock_speed_value(speed) == 0})
                    return result
                original_set = original
            elif name == 'push_speed':
                def wrapped(self, speed, *args, **kwargs):
                    if _clock_apply_bypass:
                        return original_push(self, speed, *args, **kwargs)
                    if _sidecar_role == 'client':
                        _clock_request(speed=speed)
                        return None
                    result = original_push(self, speed, *args, **kwargs)
                    bridge.emit('clock.state', {'speed': _clock_speed_value(speed), 'paused': _clock_speed_value(speed) == 0})
                    return result
                original_push = original
            else:
                def wrapped(self, speed, *args, **kwargs):
                    if _clock_apply_bypass:
                        return original_pop(self, speed, *args, **kwargs)
                    if _sidecar_role == 'client':
                        _clock_request(paused=False)
                        return None
                    result = original_pop(self, speed, *args, **kwargs)
                    bridge.emit('clock.state', {'speed': _clock_speed_value(speed), 'paused': False})
                    return result
                original_pop = original
            wrapped._kermp_wrapped = True
            wrapped._kermp_original = original
            setattr(game_clock, name, wrapped)
            _record_patch(game_clock, name, original)
        _clock_hooks_installed = True
        _clock_status['installed'] = True
        return True
    except Exception as exc:
        _clock_status['last_error'] = '%s: %s' % (type(exc).__name__, exc)
        return False


def clock_status():
    return dict(_clock_status)


def _configure_simulation_authority(role):
    """Install authoritative-client simulation suppression for the client role.

    The host never suppresses its own simulation. A client installs a reversible
    wrapper on ``scheduling.Timeline.simulate`` that suppresses normal gameplay
    simulation but runs the original during travel loading (bypass). The patch is
    role-checked at call time and participates in the ``_patches`` teardown.
    """
    global _simulation_status
    _simulation_status = {'role': role or 'unknown', 'timeline_suppression_available': False,
                          'installed': False, 'local_simulation_enabled': role != 'client',
                          'clock_source': 'host' if role == 'client' else 'local',
                          'bypass': bool(_simulation_bypass), 'last_error': None}
    if role == 'client':
        _install_timeline_suppression()
    bridge.emit('simulation.authority', dict(_simulation_status))


def _install_timeline_suppression():
    global _timeline_suppression_installed
    if _timeline_suppression_installed:
        return True
    try:
        import scheduling
        timeline = getattr(scheduling, 'Timeline', None)
        if timeline is None:
            _simulation_status['last_error'] = 'timeline_missing'
            return False
        original = getattr(timeline, 'simulate', None)
        if not callable(original):
            _simulation_status['last_error'] = 'timeline_simulate_missing'
            return False
        if getattr(original, '_kermp_wrapped', False):
            _timeline_suppression_installed = True
            _simulation_status.update({'timeline_suppression_available': True, 'installed': True,
                                       'local_simulation_enabled': False, 'last_error': None})
            return True

        def wrapped(*args, **kwargs):
            if _sidecar_role == 'client' and not _simulation_bypass:
                return None
            return original(*args, **kwargs)

        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        timeline.simulate = wrapped
        _record_patch(timeline, 'simulate', original)
        _timeline_suppression_installed = True
        _simulation_status.update({'timeline_suppression_available': True, 'installed': True,
                                   'local_simulation_enabled': False, 'last_error': None})
        _log('KERMP simulation suppression installed (client)')
        return True
    except Exception as exc:
        _simulation_status['last_error'] = 'timeline_install:%s: %s' % (type(exc).__name__, exc)
        return False


def set_simulation_bypass(active):
    """Toggle the travel-loading simulation bypass (client role only)."""
    global _simulation_bypass
    _simulation_bypass = bool(active)
    _simulation_status['bypass'] = bool(active)


def simulation_status():
    return dict(_simulation_status)


def _view_update_message_id():
    try:
        from protocolbuffers import Consts_pb2
        return int(getattr(Consts_pb2, 'MSG_OBJECTS_VIEW_UPDATE'))
    except Exception:
        return None


def _local_only_message_ids():
    """Resolve client-local UI message ids from the installed build (S4MP model)."""
    global _local_only_ids
    if _local_only_ids is not None:
        return _local_only_ids
    ids = set()
    try:
        from protocolbuffers import Consts_pb2
        for name in ('MSG_OBJECT_IS_INTERACTABLE', 'MSG_PIE_MENU_CREATE', 'MSG_PHONE_MENU_CREATE',
                     'MSG_UI_DIALOG_SHOW', 'MSG_GAME_SAVE_LOCK_UNLOCK', 'MSG_SHOW_SIM_PROFILE'):
            value = getattr(Consts_pb2, name, None)
            if isinstance(value, int):
                ids.add(value)
    except Exception:
        pass
    _local_only_ids = ids
    return ids


def _classify_message(msg_id):
    """Return 'local_only' or 'replicate' for a host outgoing message."""
    if int(msg_id) in _local_only_message_ids():
        return 'local_only'
    return 'replicate'


def _local_only_operation_ids():
    """Resolve S4MP-compatible client-local Distributor operation types."""
    global _local_only_op_ids
    if _local_only_op_ids is not None:
        return _local_only_op_ids
    ids = set()
    try:
        from protocolbuffers.DistributorOps_pb2 import Operation
        for name in (
                'FOCUS', 'HOVERTIP_CREATED', 'SET_SIM_ACTIVE', 'SET_VFX_MASK',
                'CLIENT_CREATE', 'CLIENT_DELETE', 'SET_GAME_TIME',
                'LIVE_DRAG_START', 'LIVE_DRAG_END', 'LIVE_DRAG_CANCEL',
                'SELECT_CAREER_UI', 'SHOW_BILLS_DIALOG', 'SITUATION_CALLBACK_RESPONSE',
                'MSG_SIM_PERSONALITY_ASSIGNMENT', 'TAKE_PHOTO', 'UI_LIGHT_COLOR_SHOW',
                'OPEN_INVENTORY', 'NOTEBOOK_VIEW', 'DYNAMIC_SIGN_VIEW',
                'COMMUNITY_POLICY_BOARD', 'UNIVERSITY_ENROLLMENT_WIZARD', 'BOOK_VIEW',
                'END_OF_WORKDAY', 'SHOW_SOCIAL_MEDIA_DIALOG', 'SEND_UI_MESSAGE',
                'RETAIL_BALANCE_TRANSFER_DIALOG', 'BUSINESS_SUMMARY_DIALOG',
                'SHOW_HORSE_COMPETITION_SELECTOR', 'SHOW_RENTAL_UNIT_MANAGEMENT',
                'SHOW_LIFETIME_MILESTONES_PANEL', 'CUSTOM_SCHEDULE_SET_CUSTOM_SCHEDULE',
                'CUSTOM_SCHEDULE_SET_CUSTOM_SET_SCHEDULE_LIST',
                'CUSTOM_SCHEDULE_SET_CUSTOM_SET_ASSIGNMENT_LIST',
                'MSG_SITUATION_GETAWAY_RULES', 'SET_RESIDENT_LIST',
                'CUSTOM_SCHEDULE_SET_CUSTOM_ASSIGNMENT'):
            value = getattr(Operation, name, None)
            if isinstance(value, int):
                ids.add(value)
    except Exception:
        pass
    _local_only_op_ids = ids
    return ids


def _filtered_remote_message(message):
    """Copy a ViewUpdate and remove client-local ops from the LAN copy only."""
    try:
        from protocolbuffers import Distributor_pb2
        view_update_type = getattr(Distributor_pb2, 'ViewUpdate', None)
        if view_update_type is None or not isinstance(message, view_update_type):
            return message
    except Exception:
        return message
    try:
        filtered = view_update_type()
        filtered.CopyFrom(message)
        local_ids = _local_only_operation_ids()
        removed = 0
        for entry in list(filtered.entries):
            operations = getattr(getattr(entry, 'operation_list', None), 'operations', None)
            if operations is None:
                continue
            for index in range(len(operations) - 1, -1, -1):
                if int(getattr(operations[index], 'type', -1)) in local_ids:
                    del operations[index]
                    removed += 1
            if len(operations) == 0:
                filtered.entries.remove(entry)
        _message_stats['dropped_local_ops'] += removed
        return filtered
    except Exception as exc:
        _message_stats['last_error'] = 'remote_op_filter: %s' % exc
        return message


def message_capture_status():
    return dict(_message_stats)


def _install_game_message_capture():
    """Observe host Client.send_message and replicate non-local game messages.

    Host-originated messages are classified: client-local UI messages are never
    replicated; everything else (ViewUpdate and other authoritative state) is
    serialized, bounded, and forwarded through the sidecar.
    """
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
                _message_stats['observed'] += 1
                _message_stats['last_msg_id'] = int(msg_id)
                classification = _classify_message(msg_id)
                if classification == 'local_only':
                    _message_stats['dropped_local'] += 1
                    return result
                message = kwargs.get('msg')
                if message is None:
                    message = kwargs.get('message')
                if message is None and len(args) > 1:
                    message = args[1]
                serializer = getattr(message, 'SerializeToString', None)
                if not callable(serializer):
                    _message_stats['dropped_unserializable'] += 1
                    return result
                raw = _filtered_remote_message(message).SerializeToString()
                if not isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw)
                if len(raw) > _MAX_RAW_GAME_MESSAGE_BYTES:
                    _message_stats['dropped_oversize'] += 1
                    return result
                sent = bridge.emit('game.raw_message', {
                    'msg_id': int(msg_id),
                    'payload_b64': base64.b64encode(raw).decode('ascii'),
                })
                if sent:
                    _view_updates_sent += 1
                    _message_stats['replicated'] += 1
                    _message_stats['by_id'][int(msg_id)] = _message_stats['by_id'].get(int(msg_id), 0) + 1
                    _last_view_update_size = len(raw)
                    _last_view_update_msg_id = int(msg_id)
            except Exception:
                _message_stats['last_error'] = traceback.format_exc()
                _log('KERMP DISTRIBUTOR CAPTURE ERROR %s' % _message_stats['last_error'])
            return result

        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        Client.send_message = wrapped
        _record_patch(Client, 'send_message', original)
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


def _coerce_id(value):
    """Best-effort int id extraction from Sims param/instance objects."""
    if value is None:
        return None
    for name in ('target_id', 'guid64', 'guid', 'id'):
        try:
            v = getattr(value, name, None)
            if v is not None:
                return int(v)
        except Exception:
            pass
    try:
        return int(value)
    except Exception:
        return None


def interaction_status():
    return dict(_interaction_stats)


def _install_interaction_interception():
    """Forward client interaction pushes to the host instead of local execution.

    On a client, ``Sim.push_super_affordance`` is the funnel for pie-menu
    selections (Sit, Go Here, object and social interactions). The client never
    executes these locally: the (affordance, target, sim) tuple is normalized to
    ids and emitted as an ``interaction.request`` for host-authoritative
    execution. The host role is unaffected.
    """
    global _interaction_interception_installed
    if _interaction_interception_installed:
        return True
    try:
        from sims.sim import Sim
        original = getattr(Sim, 'push_super_affordance', None)
        if not callable(original):
            _interaction_stats['last_error'] = 'push_super_affordance_missing'
            return False
        if getattr(original, '_kermp_wrapped', False):
            _interaction_interception_installed = True
            return True

        def wrapped(self, affordance=None, *args, **kwargs):
            if _sidecar_role != 'client':
                return original(self, affordance, *args, **kwargs)
            try:
                affordance_id = _coerce_id(affordance)
                target = kwargs.get('target')
                if target is None and args:
                    target = args[0]
                target_id = _coerce_id(target)
                sim_id = _coerce_id(getattr(self, 'sim_info', None)) or _coerce_id(self)
                if affordance_id is None:
                    _interaction_stats['dropped'] += 1
                    _interaction_stats['last_error'] = 'affordance_id_unresolved'
                    return None
                _interaction_stats['forwarded'] += 1
                _interaction_stats['last_affordance_id'] = affordance_id
                _interaction_stats['last_target_id'] = target_id
                _interaction_stats['last_sim_id'] = sim_id
                request_id = 'local-%s' % __import__('uuid').uuid4().hex
                _interaction_request_by_id[request_id] = {'sim_id': str(sim_id) if sim_id is not None else ''}
                sent = bridge.emit('interaction.request', {
                    'request_id': request_id,
                    'affordance_id': str(affordance_id),
                    'target_id': str(target_id) if target_id is not None else '0',
                    'sim_id': str(sim_id) if sim_id is not None else '',
                })
                if sent:
                    _interaction_stats['sent'] += 1
                else:
                    _interaction_stats['last_error'] = 'bridge_disconnected'
                _log('KERMP INTERACTION FORWARDED affordance=%s target=%s sim=%s' %
                     (affordance_id, target_id, sim_id))
                return None
            except Exception:
                _interaction_stats['last_error'] = traceback.format_exc()
                _log('KERMP INTERACTION FORWARD ERROR %s' % _interaction_stats['last_error'])
                return None

        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        Sim.push_super_affordance = wrapped
        _record_patch(Sim, 'push_super_affordance', original)
        _interaction_interception_installed = True
        _log('KERMP interaction interception installed Sim.push_super_affordance')
        return True
    except Exception:
        _interaction_stats['last_error'] = traceback.format_exc()
        _log('KERMP interaction interception unavailable: %s' % _interaction_stats['last_error'])
        return False


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
        msg_id = int(payload['msg_id'])
        # Current builds expose the distributor transport as the global omega
        # module.  Keep the client.omega path only as a compatibility fallback.
        try:
            import omega as omega_module
        except Exception:
            omega_module = None
        send = getattr(omega_module, 'send', None) if omega_module is not None else None
        if not callable(send):
            client_omega = getattr(client, 'omega', None)
            send = getattr(client_omega, 'send', None)
            if not callable(send):
                raise ValueError('global_omega_send_unavailable')
            send(msg_id, raw)
            path = 'client.omega.send(msg_id, raw)'
        else:
            send(int(getattr(client, 'id')), msg_id, raw)
            path = 'omega.send(client.id, msg_id, raw)'
        _last_view_update_size = len(raw)
        _last_view_update_msg_id = msg_id
        _log('KERMP DISTRIBUTOR APPLY path=%s msg_id=%s size=%s' % (path, msg_id, len(raw)))
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
        'global_omega_type': None, 'global_omega_send_callable': False,
        'consts': {}, 'error': None,
    }

    def interesting(names):
        needles = ('op', 'message', 'view', 'send', 'process', 'flush', 'distribut')
        return sorted(name for name in names
                      if not name.startswith('__') and any(n in name.lower() for n in needles))[:60]

    try:
        import omega as omega_module
        result['global_omega_type'] = type(omega_module).__name__
        result['global_omega_send_callable'] = callable(getattr(omega_module, 'send', None))
    except Exception as exc:
        result['global_omega_error'] = '%s: %s' % (type(exc).__name__, exc)

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


def _sim_selection_state(payload):
    global _authoritative_sim_id
    payload = payload or {}
    if payload.get('player_id') in ('local', _sidecar_role, _sidecar_player_id):
        _authoritative_sim_id = payload.get('sim_id')
    sim_id = str(payload.get('sim_id') or '')
    if sim_id:
        _authoritative_controllers[sim_id] = list(payload.get('controllers') or [])
    if _sidecar_role == 'client' and payload.get('player_id') not in ('local', _sidecar_role, _sidecar_player_id):
        _restore_local_active_sim()


def _sim_state(payload):
    global _authoritative_sim_id, _authoritative_controllers
    payload = payload or {}
    _authoritative_controllers = {}
    for item in payload.get('sims') or []:
        sim_id = str(item.get('sim_id'))
        _authoritative_controllers[sim_id] = list(item.get('controllers') or [])
    for player in payload.get('players') or []:
        if player.get('player_id') in ('local', _sidecar_role, _sidecar_player_id):
            _authoritative_sim_id = player.get('active_sim_id')
            break


def _interaction_accepted(_payload):
    _interaction_stats['accepted'] += 1


def _interaction_rejected(payload):
    _interaction_stats['rejected'] += 1
    _interaction_stats['last_error'] = str((payload or {}).get('reason') or 'rejected')
    if _sidecar_role == 'host':
        _interaction_stats['apply_rejected'] += 1


def _interaction_started(payload):
    _interaction_stats['started'] += 1
    if _sidecar_role == 'host':
        _interaction_stats['apply_accepted'] += 1
    payload = payload or {}
    request_id = str(payload.get('request_id') or '')
    interaction_id = payload.get('interaction_id')
    if request_id and interaction_id and request_id in _interaction_request_by_id:
        _interaction_request_by_id[request_id]['interaction_id'] = str(interaction_id)


def _interaction_cancel(payload):
    if _sidecar_role != 'host':
        return
    request_id = str((payload or {}).get('request_id') or '')
    sim_id = str((payload or {}).get('sim_id') or '')
    try:
        import services
        info = services.sim_info_manager().get(int(sim_id))
        sim = info.get_sim_instance(allow_hidden_flags=True) if info else None
        if sim is None:
            raise ValueError('sim_not_loaded')
        interaction_id = int(str((payload or {}).get('interaction_id') or '0'))
        queue = getattr(sim, 'queue', None)
        interaction = None
        finder = getattr(queue, 'find_interaction_by_id', None) if queue is not None else None
        if callable(finder):
            interaction = finder(interaction_id)
        if interaction is None:
            finder = getattr(sim, 'find_interaction_by_id', None)
            if callable(finder):
                interaction = finder(interaction_id)
        if interaction is None:
            raise ValueError('interaction_not_found')
        cancel = getattr(interaction, 'cancel_user', None)
        if not callable(cancel):
            raise ValueError('cancel_unavailable')
        cancel('KerMP remote client cancellation')
        bridge.emit('interaction.finished', {'request_id': request_id, 'interaction_id': str(interaction_id),
                                             'status': 'cancelled'})
    except Exception as exc:
        bridge.emit('interaction.rejected', {'request_id': request_id, 'reason': '%s: %s' %
                                             (type(exc).__name__, exc)})


def _restore_local_active_sim():
    if not _local_active_sim_id:
        return False
    try:
        import services
        client = services.get_first_client()
        for name in ('set_active_sim_by_id', 'set_active_sim'):
            method = getattr(type(client), name, None) if client is not None else None
            original = getattr(method, '_kermp_original', None)
            if callable(original):
                if name.endswith('_by_id'):
                    original(client, int(_local_active_sim_id))
                else:
                    info = services.sim_info_manager().get(int(_local_active_sim_id))
                    sim = info.get_sim_instance(allow_hidden_flags=True) if info else None
                    if sim is not None:
                        original(client, sim)
                return True
    except Exception:
        _log('KERMP local active Sim restore failed: %s' % traceback.format_exc())
    return False


def _interaction_finished(_payload):
    return None


def _publish_active_sim_if_ready():
    """Publish the local observation only after the live roster contains it."""
    global _last_published_active_sim_id, _local_active_sim_id
    if _sidecar_role not in ('host', 'client'):
        return False
    sims = enumerate_sims()
    active_id = _active_sim_id()
    _local_active_sim_id = active_id
    bridge.emit('sims.state', {'sims': sims, 'active_sim_id': active_id})
    if not active_id or active_id not in {item['sim_id'] for item in sims}:
        return False
    if active_id == _last_published_active_sim_id:
        return True
    _last_published_active_sim_id = active_id
    return bridge.emit('sim.select', {'sim_id': active_id, 'local': True})


def _install_active_sim_hooks():
    global _active_sim_hook_installed
    if _active_sim_hook_installed:
        return True
    try:
        import services
        client = services.get_first_client()
        client_type = type(client) if client is not None else None
        if client_type is None:
            return False
        installed = False
        for name in ('set_active_sim_by_id', 'set_active_sim'):
            original = getattr(client_type, name, None)
            if not callable(original) or getattr(original, '_kermp_wrapped', False):
                continue
            def make_wrapper(method):
                def wrapped(self, *args, **kwargs):
                    result = method(self, *args, **kwargs)
                    try:
                        _publish_active_sim_if_ready()
                    except Exception:
                        _log(traceback.format_exc())
                    return result
                wrapped._kermp_wrapped = True
                wrapped._kermp_original = method
                return wrapped
            setattr(client_type, name, make_wrapper(original))
            _record_patch(client_type, name, original)
            installed = True
        _active_sim_hook_installed = installed
        return installed
    except Exception:
        _log('KERMP active Sim hook unavailable: %s' % traceback.format_exc())
        return False


def _install_cancel_interception():
    """Route the stable cancel_super_interaction command from client to host."""
    global _cancel_interception_installed
    if _cancel_interception_installed:
        return True
    try:
        from server_commands import interaction_commands
        original = getattr(interaction_commands, 'cancel_super_interaction', None)
        if not callable(original) or getattr(original, '_kermp_wrapped', False):
            return False
        def wrapped(super_interaction_id, context_handle, *args, **kwargs):
            if _sidecar_role != 'client':
                return original(super_interaction_id, context_handle, *args, **kwargs)
            sim_id = _active_sim_id()
            request_id = None
            for rid, item in _interaction_request_by_id.items():
                if item.get('sim_id') == sim_id:
                    request_id = rid
            sent = bridge.emit('interaction.cancel', {
                'request_id': request_id,
                'sim_id': sim_id or '',
                'interaction_id': str(super_interaction_id),
                'context_handle': str(context_handle),
            })
            if not sent:
                _interaction_stats['last_error'] = 'bridge_disconnected'
            return None
        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        interaction_commands.cancel_super_interaction = wrapped
        _record_patch(interaction_commands, 'cancel_super_interaction', original)
        _cancel_interception_installed = True
        return True
    except Exception:
        _log('KERMP cancel interception unavailable: %s' % traceback.format_exc())
        return False


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
    _interaction_stats['delivered'] += 1
    _interaction_stats['last_request_id'] = request_id
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
        set_simulation_bypass(True)
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
        set_simulation_bypass(True)
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
    set_simulation_bypass(False)


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
    set_simulation_bypass(False)
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
    # If KerMP installed the natural-travel wrapper, invoke/introspect the
    # original Sims command rather than recursively inspecting our *args/**kwargs
    # wrapper.
    original = getattr(fn, '_kermp_original', None)
    if callable(original):
        fn = original
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
    from server_commands.argument_helpers import OptionalTargetParam
    try:
        import services
        client = services.get_first_client()
        active = getattr(client, 'active_sim_info', None) if client else None
        active_id = int(active.id) if active is not None else None
    except Exception:
        active_id = None
    if active_id is None:
        raise ValueError('travel_actor_not_found')
    traveling = [sim_id for sim_id in actor_ids if int(sim_id) != active_id]
    opt_sim = OptionalTargetParam(str(active_id))
    _travel_native_bypass = True
    try:
        return fn(opt_sim, zone_id, *traveling)
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
        _record_patch(travel_commands, 'travel_sims_to_zone', original)
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


def capture_object_build_operation(op, data):
    """Common natural Build/Buy capture boundary for all object wrappers."""
    if _build_capture_depth:
        return False
    payload = {'op': op, 'data': data}
    try:
        operation = adapter.capture_local(payload)
    except Exception:
        _log('KERMP BUILD OBJECT CAPTURE ERROR %s' % traceback.format_exc())
        return False
    if not operation:
        return False
    _log('KERMP BUILD OBJECT CAPTURE type=%s object_id=%s data=%s' %
         (op, data.get('object_id', 'none'), _bounded_repr(data)))
    bridge.emit('build.operation', operation)
    return True


def _bounded_repr(value):
    return repr(value)[:2000]


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
    global _wall_callback_registered, _wall_callback_registration
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
        _wall_callback_registration = (callbacks, method_name)
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


def _install_object_build_hooks():
    """Install wrappers at the current-build Python Build/Buy boundaries."""
    global _build_hook_info
    candidates = {
        'create_hook': ('objects.system', 'c_api_create_object'),
        'move_hook': ('build_buy', 'c_api_set_object_location_ex'),
        'funds_hook': ('build_buy', 'c_api_modify_household_funds'),
        'destroy_hook': ('objects.system', 'c_api_destroy_object'),
        'parent_hook': ('objects.system', 'c_api_set_parent_object'),
        'clear_parent_hook': ('objects.system', 'c_api_clear_parent_object'),
    }
    result = {}
    for label, (module_name, attr) in candidates.items():
        try:
            module = __import__(module_name, fromlist=[attr])
            value = getattr(module, attr, None)
            result[label] = {'available': callable(value),
                             'signature': _safe_signature(value) if callable(value) else None}
        except Exception as exc:
            result[label] = {'available': False, 'error': type(exc).__name__}
    try:
        from objects.client_object_mixin import ClientObjectMixin
        mixin = ClientObjectMixin
    except Exception:
        mixin = None
    for label, attr in (('definition_hook', 'set_definition'), ('scale_hook', '_resend_client_scale')):
        value = getattr(mixin, attr, None) if mixin is not None else None
        result[label] = {'available': callable(value),
                         'signature': _safe_signature(value) if callable(value) else None}
    _build_hook_info = result
    adapter.hooks = result
    adapter.configure(apply=_apply_object_operation)
    _install_wrapper('move_hook', _wrap_move)
    _install_wrapper('funds_hook', _wrap_funds)
    _install_wrapper('create_hook', _wrap_create)
    _install_wrapper('destroy_hook', _wrap_destroy)
    _install_wrapper('parent_hook', _wrap_parent)
    _install_wrapper('clear_parent_hook', _wrap_clear_parent)
    _install_wrapper('definition_hook', _wrap_definition)
    _install_wrapper('scale_hook', _wrap_scale)
    _log('KERMP BUILD OBJECT HOOKS %s' % result)


def _install_wrapper(label, factory):
    info = _build_hook_info.get(label) or {}
    if not info.get('available'):
        return False
    try:
        module_name, attr = {
            'move_hook': ('build_buy', 'c_api_set_object_location_ex'),
            'funds_hook': ('build_buy', 'c_api_modify_household_funds'),
            'create_hook': ('objects.system', 'c_api_create_object'),
            'destroy_hook': ('objects.system', 'c_api_destroy_object'),
            'parent_hook': ('objects.system', 'c_api_set_parent_object'),
            'clear_parent_hook': ('objects.system', 'c_api_clear_parent_object'),
        }.get(label, ('objects.client_object_mixin',
                      'set_definition' if label == 'definition_hook' else '_resend_client_scale'))
        module = __import__(module_name, fromlist=[attr])
        if label in ('definition_hook', 'scale_hook'):
            target = getattr(module, 'ClientObjectMixin')
        else:
            target = module
        original = getattr(target, attr)
        if getattr(original, '_kermp_wrapped', False):
            return True
        wrapped = factory(original)
        wrapped = functools.wraps(original)(wrapped)
        wrapped._kermp_wrapped = True
        wrapped._kermp_original = original
        setattr(target, attr, wrapped)
        _record_patch(target, attr, original)
        _build_hook_info[label]['installed'] = True
        return True
    except Exception as exc:
        _build_hook_info[label]['install_error'] = '%s: %s' % (type(exc).__name__, exc)
        return False


def _wrap_move(original):
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        values = bind_call(original, args, kwargs,
                           ('zone_id', 'obj_id', 'routing_surface', 'transform',
                            'parent_id', 'parent_type_info', 'slot_hash'))
        capture_object_build_operation('object.move', {
            'zone_id': values.get('zone_id'),
            'object_id': values.get('obj_id', values.get('object_id')),
            'routing_surface': _json_value(values.get('routing_surface')),
            'transform': _serialize_transform(values.get('transform')),
            'parent_id': values.get('parent_id'),
            'parent_type_info': _json_value(values.get('parent_type_info')),
            'slot_hash': values.get('slot_hash'),
        })
        return result
    return wrapped


def _wrap_funds(original):
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        values = bind_call(original, args, kwargs, ('amount', 'household_id', 'reason', 'zone_id'))
        capture_object_build_operation('funds.modify', {
            'amount': values.get('amount'), 'household_id': values.get('household_id'),
            'reason': values.get('reason'), 'zone_id': values.get('zone_id'),
        })
        return result
    return wrapped


def _wrap_create(original):
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        values = bind_call(original, args, kwargs,
                           ('zone_id', 'def_id', 'obj_id', 'obj_state', 'loc_type', 'content_source'))
        obj = result if hasattr(result, 'id') else None
        object_id = values.get('obj_id', values.get('object_id'))
        object_id = object_id if object_id is not None else getattr(obj, 'id', None)
        capture_object_build_operation('object.create', {
            'zone_id': values.get('zone_id'), 'object_id': object_id,
            'definition_id': values.get('def_id', values.get('definition_id')),
            'object_state': _json_value(values.get('obj_state', values.get('object_state'))),
            'location_type': _json_value(values.get('loc_type', values.get('location_type'))),
            'content_source': _json_value(values.get('content_source')),
            'transform': _serialize_transform(getattr(obj, 'location', None)),
        })
        return result
    return wrapped


def _wrap_destroy(original):
    def wrapped(*args, **kwargs):
        values = bind_call(original, args, kwargs, ('zone_id', 'obj_or_id'))
        target = values.get('obj_or_id', values.get('object_or_id'))
        object_id = getattr(target, 'id', target)
        result = original(*args, **kwargs)
        capture_object_build_operation('object.destroy', {
            'zone_id': values.get('zone_id'), 'object_id': object_id,
        })
        return result
    return wrapped


def _wrap_parent(original):
    def wrapped(*args, **kwargs):
        global _build_capture_depth
        _build_capture_depth += 1
        try:
            result = original(*args, **kwargs)
        finally:
            _build_capture_depth -= 1
        values = bind_call(original, args, kwargs,
                           ('obj_id', 'parent_id', 'transform', 'joint_name', 'slot_hash', 'zone_id'))
        capture_object_build_operation('object.set_parent', {
            'object_id': values.get('obj_id', values.get('object_id')), 'parent_id': values.get('parent_id'),
            'transform': _serialize_transform(values.get('transform')),
            'joint_name': values.get('joint_name'), 'slot_hash': values.get('slot_hash'),
            'zone_id': values.get('zone_id'),
        })
        return result
    return wrapped


def _wrap_clear_parent(original):
    def wrapped(*args, **kwargs):
        global _build_capture_depth
        _build_capture_depth += 1
        try:
            result = original(*args, **kwargs)
        finally:
            _build_capture_depth -= 1
        values = bind_call(original, args, kwargs, ('obj_id', 'transform', 'zone_id', 'surface'))
        capture_object_build_operation('object.clear_parent', {
            'object_id': values.get('obj_id', values.get('object_id')), 'transform': _serialize_transform(values.get('transform')),
            'zone_id': values.get('zone_id'), 'routing_surface': _json_value(values.get('surface')),
        })
        return result
    return wrapped


def _wrap_definition(original):
    def wrapped(self, definition_id, *args, **kwargs):
        previous = getattr(getattr(self, 'definition', None), 'id', None)
        result = original(self, definition_id, *args, **kwargs)
        if previous != definition_id:
            capture_object_build_operation('object.definition', {
                'object_id': getattr(self, 'id', None), 'definition_id': definition_id,
            })
        return result
    return wrapped


def _wrap_scale(original):
    def wrapped(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        capture_object_build_operation('object.scale', {
            'object_id': getattr(self, 'id', None), 'scale': getattr(self, 'scale', None),
        })
        return result
    return wrapped


def _json_value(value, depth=0):
    if depth > 5 or value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, depth + 1) for item in value]
    try:
        if hasattr(value, 'value'):
            return int(value.value)
    except Exception:
        pass
    result = {}
    for name in ('x', 'y', 'z', 'w', 'primary_id', 'secondary_id', 'type', 'level'):
        try:
            if hasattr(value, name):
                result[name] = _json_value(getattr(value, name), depth + 1)
        except Exception:
            pass
    return result or repr(value)[:500]


def _serialize_transform(transform):
    if transform is None:
        return None
    position = getattr(transform, 'position', getattr(transform, 'translation', None))
    orientation = getattr(transform, 'orientation', None)
    surface = getattr(transform, 'routing_surface', None)
    result = {
        'translation': _components(position, 3),
        'orientation': _components(orientation, 4),
        'routing_surface': _json_value(surface),
    }
    if result['translation'] is None and result['orientation'] is None:
        return _json_value(transform)
    return result


def _components(value, count):
    if value is None:
        return None
    names = ('x', 'y', 'z', 'w')[:count]
    try:
        return [float(getattr(value, name)) for name in names]
    except Exception:
        if isinstance(value, (list, tuple)) and len(value) >= count:
            return [float(item) for item in value[:count]]
        return None


def _deserialize_transform(data, fallback=None):
    fallback_transform = getattr(fallback, 'transform', fallback)
    if not isinstance(data, dict):
        _log('KERMP TRANSFORM DESERIALIZE skipped reason=not_dict type=%s' % type(data).__name__)
        return fallback_transform
    try:
        from sims4.math import Vector3, Quaternion, Transform
        position = data.get('position') or data.get('translation')
        orientation = data.get('orientation')
        if isinstance(position, (list, tuple)):
            position = Vector3(float(position[0]), float(position[1]), float(position[2]))
        elif isinstance(position, dict):
            position = Vector3(float(position.get('x', 0)), float(position.get('y', 0)), float(position.get('z', 0)))
        if isinstance(orientation, (list, tuple)):
            orientation = Quaternion(float(orientation[0]), float(orientation[1]),
                                     float(orientation[2]), float(orientation[3]))
        elif isinstance(orientation, dict):
            orientation = Quaternion(float(orientation.get('x', 0)), float(orientation.get('y', 0)),
                                     float(orientation.get('z', 0)), float(orientation.get('w', 1)))
        return Transform(position, orientation)
    except Exception as exc:
        _log('KERMP TRANSFORM DESERIALIZE ERROR %s: %s' % (type(exc).__name__, exc))
        return fallback_transform


def _deserialize_routing_surface(value, fallback=None):
    if value is None:
        return getattr(fallback, 'routing_surface', None) if fallback is not None else None
    if not isinstance(value, dict):
        return value
    import routing
    try:
        return routing.SurfaceIdentifier(int(value.get('primary_id', 0)),
                                         int(value.get('secondary_id', 0)),
                                         int(value.get('type', 0)))
    except Exception as exc:
        _log('KERMP ROUTING SURFACE DESERIALIZE ERROR %s: %s' % (type(exc).__name__, exc))
        return getattr(fallback, 'routing_surface', None) if fallback is not None else None


def _deserialize_location(data, fallback=None):
    if not isinstance(data, dict):
        _log('KERMP LOCATION DESERIALIZE skipped reason=not_dict type=%s' % type(data).__name__)
        return fallback
    try:
        import routing
        transform = _deserialize_transform(data, fallback)
        if transform is None:
            _log('KERMP LOCATION DESERIALIZE failed reason=transform_unavailable')
            return fallback
        surface = _deserialize_routing_surface(data.get('routing_surface'), fallback)
        return routing.Location(transform, surface)
    except Exception as exc:
        _log('KERMP LOCATION DESERIALIZE ERROR %s: %s' % (type(exc).__name__, exc))
        return fallback


def _position_matches(obj, transform_data, tolerance=0.001):
    if not isinstance(transform_data, dict):
        return False
    expected = transform_data.get('translation') or transform_data.get('position')
    actual = _components(getattr(obj, 'position', None), 3)
    if not isinstance(expected, (list, tuple)) or actual is None or len(expected) < 3:
        return False
    try:
        return all(abs(float(actual[index]) - float(expected[index])) <= tolerance
                   for index in range(3))
    except Exception:
        return False


def build_object_status():
    return {'hooks': _build_hook_info, 'adapter': adapter.status(),
            'last_error': _build_last_error}


def _object_value(obj):
    """Best-effort object sell value; fails closed to None."""
    if obj is None:
        return None
    for name in ('current_value', 'catalog_value', 'price'):
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def _object_household_id(obj):
    if obj is None:
        return None
    getter = getattr(obj, 'get_household_owner_id', None)
    if callable(getter):
        try:
            household_id = getter()
        except Exception:
            household_id = None
        if household_id not in (None, 0):
            return int(household_id)
    household_id = getattr(obj, 'household_owner_id', None)
    if household_id in (None, 0):
        return None
    try:
        return int(household_id)
    except (TypeError, ValueError):
        return None


def _apply_object_operation(operation):
    """Apply only operations whose current-build Python object API is explicit."""
    global _build_last_error
    data = operation.get('data') or {}
    op = operation.get('op')
    try:
        import services
        zone_id = data.get('zone_id')
        if zone_id is None:
            zone_id = services.current_zone_id()
        zone_id = int(str(zone_id))
        object_id = int(str(data.get('object_id'))) if data.get('object_id') is not None else None
        if op == 'object.create':
            import objects.system
            result = objects.system.c_api_create_object(
                zone_id, int(str(data.get('definition_id'))), object_id,
                data.get('object_state', 0), data.get('location_type', 1),
                data.get('content_source', 0))
            obj = services.object_manager().get(object_id) if object_id else None
            if obj is None and hasattr(result, 'id'):
                obj = result
            if obj is not None and data.get('transform'):
                _apply_object_location(obj, object_id or getattr(obj, 'id', None), zone_id, data)
            return True
        if op == 'object.destroy':
            # Household funds are authoritative and captured as a separate
            # `funds.modify` operation by the host; a destroy apply must never
            # mutate funds (see BUILD_BUY_OBJECT_CAPTURE.md). Refunding here would
            # double-credit on the host and credit a failed destroy.
            import objects.system
            return bool(objects.system.c_api_destroy_object(zone_id, object_id))
        obj = services.object_manager().get(object_id)
        if obj is None:
            raise ValueError('object_not_found:%s' % object_id)
        if op == 'object.definition':
            setter = getattr(obj, 'set_definition', None)
            if not callable(setter):
                raise ValueError('set_definition_unavailable')
            setter(int(str(data.get('definition_id'))), True)
        elif op == 'object.scale':
            if not hasattr(obj, 'scale'):
                raise ValueError('scale_unavailable')
            obj.scale = float(data.get('scale'))
        elif op == 'object.set_parent':
            import objects.system
            objects.system.c_api_set_parent_object(
                object_id, int(str(data.get('parent_id'))),
                _deserialize_transform(data.get('transform'), getattr(obj, 'location', None)),
                data.get('joint_name'), data.get('slot_hash'), zone_id)
        elif op == 'object.clear_parent':
            import objects.system
            objects.system.c_api_clear_parent_object(
                object_id, _deserialize_transform(data.get('transform'), getattr(obj, 'location', None)),
                zone_id, data.get('routing_surface'))
        elif op == 'object.move':
            _apply_object_location(obj, object_id, zone_id, data)
        elif op == 'funds.modify':
            raise ValueError('funds.modify is host-authoritative and not a client object apply')
        else:
            raise ValueError('unsupported object apply: %s' % op)
    except Exception as exc:
        _build_last_error = '%s: %s' % (type(exc).__name__, exc)
        raise


def _apply_object_location(obj, object_id, zone_id, data):
    import build_buy
    previous = getattr(obj, 'location', None)
    transform_data = data.get('transform')
    transform = _deserialize_transform(transform_data, previous)
    if transform is None:
        raise ValueError('transform_unavailable data=%s' % _bounded_repr(transform_data))
    routing_surface = _deserialize_routing_surface(data.get('routing_surface'), previous)
    parent_id, parent_type_info, slot_hash = resolve_parent_context(data)
    try:
        native_result = build_buy.c_api_set_object_location_ex(
            zone_id, object_id, routing_surface, transform,
            parent_id, parent_type_info, slot_hash)
    except Exception as exc:
        raise ValueError('native_set_location_failed:%s:%s parent_id=%s parent_type_info=%s slot_hash=%s' %
                         (type(exc).__name__, exc, parent_id, parent_type_info, slot_hash))
    if _position_matches(obj, transform_data):
        return True
    location = _deserialize_location(transform_data, previous)
    if location is None:
        raise ValueError('location_unavailable transform=%s' % _bounded_repr(transform_data))
    try:
        obj.location = location
    except Exception as exc:
        raise TypeError('location_assignment:%s:%s' % (type(exc).__name__, exc))
    if _position_matches(obj, transform_data):
        return True
    resend = getattr(obj, 'resend_location', None)
    if callable(resend):
        try:
            resend()
        except Exception as exc:
            raise TypeError('location_resend:%s:%s' % (type(exc).__name__, exc))
    if _position_matches(obj, transform_data):
        return True
    expected = (transform_data or {}).get('translation') or (transform_data or {}).get('position')
    actual = _components(getattr(obj, 'position', None), 3)
    raise ValueError(
        'move_postcondition_failed expected_position=%s actual_position=%s native_result=%s '
        'parent_id=%s parent_type_info=%s slot_hash=%s routing_surface=%s' %
        (expected, actual, repr(native_result), parent_id, parent_type_info, slot_hash,
         _json_value(routing_surface)))


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
    wrapped._kermp_original = original
    Zone.do_zone_spin_up = wrapped
    _record_patch(Zone, 'do_zone_spin_up', original)


def _after_zone_spin_up():
    global _pending_travel_txn, _travel_batch_complete, _travel_local_zone_loaded
    if not _pending_travel_txn:
        _publish_active_sim_if_ready()
        return
    try:
        import services
        zone_id = services.current_zone_id()
    except Exception:
        zone_id = 0
    _travel_local_zone_loaded = True
    _publish_active_sim_if_ready()
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
