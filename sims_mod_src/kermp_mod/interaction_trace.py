"""Development-only passive tracing for the current Sims interaction pipeline.

The tracer is inert until ``start()`` is called from ``kermp.trace.start``.  It
wraps a bounded set of current-build Python boundaries, records compact values,
and always delegates to the exact callable that was present at trace start.
"""

import functools
import importlib
import inspect
import json
import os
import threading
import time


_MAX_EVENTS = 512
_MAX_PER_FUNCTION = 96
_events = []
_patches = []
_errors = []
_counts = {}
_sequence = 0
_enabled = False
_started_at = None
_session = 0


_TRACE_SPECS = (
    ('server_commands.interaction_commands', None,
     ('has_choices', 'generate_choices', 'select_choice', 'push_interaction',
      'push_targeting_sim_info', 'cancel_super_interaction',
      'cancel_mixer_interaction')),
    ('server.client', 'Client',
     ('create_interaction_context', 'set_choices', 'select_interaction',
      'send_message')),
    ('interactions.choices', 'ChoiceMenu',
     ('__init__', 'add_potential_aops', 'add_aop', '_add_menu_item',
      'select', 'clear')),
    ('interactions.aop', 'AffordanceObjectPair',
     ('test', 'interaction_factory', 'execute_interaction', 'execute',
      'test_and_execute')),
    ('sims.sim', 'Sim', ('push_super_affordance',)),
    ('interactions.interaction_queue', 'InteractionQueue',
     ('append', 'remove_for_perform', 'process_one_interaction_gen',
      'run_interaction_gen', 'on_interaction_canceled')),
    ('interactions.interaction_queue', 'BucketBase', ('append', 'insert_next')),
    ('sims4.commands', None, ('execute',)),
    ('omega', None, ('send',)),
    ('distributor.system', 'Distributor',
     ('add_event', 'add_op', 'add_op_with_no_owner', 'process')),
    ('server_commands.ui_commands', None,
     ('ui_dialog_respond', 'ui_dialog_pick_result', 'ui_dialog_text_input')),
    ('scheduling', 'Timeline', ('simulate',)),
)


def _type_name(value):
    value_type = type(value)
    return '%s.%s' % (getattr(value_type, '__module__', ''),
                      getattr(value_type, '__name__', str(value_type)))


def _primitive(value, depth=0):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:120]
    if isinstance(value, bytes):
        return {'type': 'bytes', 'length': len(value)}
    if depth < 1 and isinstance(value, (tuple, list)):
        return [_primitive(item, depth + 1) for item in value[:8]]
    if depth < 1 and isinstance(value, dict):
        result = {}
        for key in list(value.keys())[:8]:
            result[str(key)[:40]] = _primitive(value[key], depth + 1)
        return result
    result = {'type': _type_name(value)}
    for name in ('id', 'sim_id', 'guid64', 'revision', 'context_handle',
                 'group_id', 'aop_id'):
        try:
            candidate = getattr(value, name, None)
            if candidate is not None and isinstance(candidate, (bool, int, float, str)):
                result[name] = candidate
        except Exception:
            pass
    return result


def _sim_id(sim):
    if sim is None:
        return None
    for value in (getattr(sim, 'sim_info', None), sim):
        for name in ('sim_id', 'id'):
            try:
                candidate = getattr(value, name, None)
                if candidate is not None:
                    return str(int(candidate))
            except Exception:
                pass
    return None


def _runtime_context(args, kwargs):
    role = 'unknown'
    try:
        from . import hooks
        role = hooks._sidecar_role or 'unknown'
    except Exception:
        pass
    connection_id = kwargs.get('_connection')
    client_id = None
    active_sim_id = None
    menu_revision = None
    menu_count = None
    try:
        import services
        manager = services.client_manager()
        candidates = []
        if connection_id is not None:
            candidates.append(connection_id)
        if args:
            candidates.append(getattr(args[0], 'id', None))
            candidates.append(args[-1] if isinstance(args[-1], int) else None)
        client = None
        for candidate in candidates:
            if candidate is None:
                continue
            possible = manager.get(candidate)
            if possible is not None:
                client = possible
                connection_id = candidate
                break
        if client is None:
            client = services.get_first_client()
        client_id = getattr(client, 'id', None) if client is not None else None
        active_sim_id = _sim_id(getattr(client, 'active_sim', None))
        menu = getattr(client, 'choice_menu', None) if client is not None else None
        menu_revision = getattr(menu, 'revision', None) if menu is not None else None
        try:
            menu_count = len(menu) if menu is not None else 0
        except Exception:
            menu_count = None
    except Exception:
        pass
    return role, connection_id, client_id, active_sim_id, menu_revision, menu_count


def _record(phase, label, args, kwargs, result=None, error=None):
    global _sequence
    if not _enabled:
        return
    count = _counts.get(label, 0) + 1
    _counts[label] = count
    limit = 12 if label == 'scheduling.Timeline.simulate' else _MAX_PER_FUNCTION
    if count > limit:
        return
    _sequence += 1
    role, connection_id, client_id, active_sim_id, menu_revision, menu_count = _runtime_context(args, kwargs)
    thread = threading.current_thread()
    event = {
        'seq': _sequence,
        'time': round(time.monotonic() - _started_at, 6) if _started_at else 0.0,
        'phase': phase,
        'function': label.rsplit('.', 1)[-1],
        'module': label.rsplit('.', 1)[0],
        'thread': getattr(thread, 'name', None),
        'thread_id': getattr(thread, 'ident', None),
        'role': role,
        'connection_id': connection_id,
        'client_id': client_id,
        'active_sim_id': active_sim_id,
        'choice_menu_revision': menu_revision,
        'choice_menu_count': menu_count,
    }
    if phase == 'enter':
        event['args'] = _primitive(args)
        event['kwargs'] = _primitive(kwargs)
    elif phase == 'exit':
        event['return_type'] = _type_name(result)
        event['return'] = _primitive(result)
    else:
        event['exception_type'] = type(error).__name__
        event['exception'] = str(error)[:200]
    if len(_events) >= _MAX_EVENTS:
        del _events[0]
    _events.append(event)


def _make_wrapper(label, original):
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        _record('enter', label, args, kwargs)
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            _record('error', label, args, kwargs, error=exc)
            raise
        _record('exit', label, args, kwargs, result=result)
        return result
    wrapped._kermp_trace_wrapper = True
    wrapped._kermp_trace_original = original
    return wrapped


def _patch(module_name, owner_name, attribute):
    module = importlib.import_module(module_name)
    owner = getattr(module, owner_name) if owner_name else module
    original = getattr(owner, attribute, None)
    if not callable(original):
        raise AttributeError('%s.%s missing' %
                             (owner_name or module_name, attribute))
    if getattr(original, '_kermp_trace_wrapper', False):
        return
    label = '%s.%s%s' % (module_name,
                         (owner_name + '.') if owner_name else '', attribute)
    setattr(owner, attribute, _make_wrapper(label, original))
    _patches.append((owner, attribute, original, label))


def start():
    global _enabled, _started_at, _sequence, _session
    if _enabled:
        return status()
    stop()
    del _events[:]
    del _errors[:]
    _counts.clear()
    _sequence = 0
    _session += 1
    _started_at = time.monotonic()
    for module_name, owner_name, attributes in _TRACE_SPECS:
        for attribute in attributes:
            try:
                _patch(module_name, owner_name, attribute)
            except Exception as exc:
                _errors.append('%s.%s.%s:%s:%s' %
                               (module_name, owner_name or '', attribute,
                                type(exc).__name__, exc))
    _enabled = True
    _record('mark', 'kermp.trace.started', (), {'session': _session})
    return status()


def mark(label):
    _record('mark', 'kermp.trace.%s' % str(label)[:80], (), {})


def stop():
    global _enabled, _started_at
    was_enabled = _enabled
    if was_enabled:
        _record('mark', 'kermp.trace.stopped', (), {})
    _enabled = False
    for owner, attribute, original, label in reversed(_patches):
        try:
            current = getattr(owner, attribute, None)
            if getattr(current, '_kermp_trace_wrapper', False):
                setattr(owner, attribute, original)
        except Exception as exc:
            _errors.append('restore:%s:%s:%s' %
                           (label, type(exc).__name__, exc))
    del _patches[:]
    _started_at = None
    return status()


def status():
    return {
        'enabled': _enabled,
        'session': _session,
        'events': len(_events),
        'patched': len(_patches),
        'errors': list(_errors),
        'counts': dict(_counts),
        'max_events': _MAX_EVENTS,
    }


def events(limit=None):
    values = list(_events)
    if limit is not None:
        values = values[-max(0, int(limit)):]
    return values


def dump_to_file(limit=None):
    try:
        import paths
        root = getattr(paths, 'USER_DATA_ROOT', None)
    except Exception:
        root = None
    if not root:
        root = os.path.join(os.path.expanduser('~'), 'Documents',
                            'Electronic Arts', 'The Sims 4')
    directory = os.path.join(str(root), 'KerMP')
    if not os.path.isdir(directory):
        os.makedirs(directory)
    path = os.path.join(directory, 'interaction-trace-session-%s.jsonl' % _session)
    with open(path, 'w', encoding='utf-8') as handle:
        for event in events(limit):
            handle.write(json.dumps(event, sort_keys=True, separators=(',', ':')))
            handle.write('\n')
    return path


def _callable_info(value):
    result = {
        'type': _type_name(value),
        'module': getattr(value, '__module__', None),
        'qualname': getattr(value, '__qualname__', None),
        'name': getattr(value, '__name__', None),
        'trace_wrapper': bool(getattr(value, '_kermp_trace_wrapper', False)),
        'kermp_wrapper': bool(getattr(value, '_kermp_wrapped', False)),
    }
    try:
        result['signature'] = str(inspect.signature(value))
    except Exception as exc:
        result['signature'] = '<%s>' % type(exc).__name__
    code = getattr(value, '__code__', None)
    if code is not None:
        result['file'] = code.co_filename
        result['line'] = code.co_firstlineno
        result['names'] = list(code.co_names[:30])
    return result


def registry_snapshot():
    import sims4.commands as commands
    terms = ('interaction', 'choice', 'select', 'picker', 'pie', 'queue', 'push')
    described = {}
    for term in terms:
        try:
            values = commands.describe(term) or []
            described[term] = [_primitive(value) for value in list(values)[:40]]
        except Exception as exc:
            described[term] = ['%s:%s' % (type(exc).__name__, exc)]
    handlers = {}
    try:
        from server_commands import interaction_commands
        for name in ('has_choices', 'generate_choices', 'select_choice',
                     'push_interaction', 'push_targeting_sim_info',
                     'cancel_super_interaction', 'cancel_mixer_interaction'):
            handlers[name] = _callable_info(getattr(interaction_commands, name, None))
    except Exception as exc:
        handlers['error'] = '%s:%s' % (type(exc).__name__, exc)
    native_registry = getattr(commands, '_commands', None)
    return {
        'registry_type': _type_name(native_registry),
        'registry_methods': sorted(name for name in dir(native_registry)
                                   if any(term in name.lower() for term in
                                          ('command', 'describe', 'execute', 'register'))),
        'described': described,
        'handlers': handlers,
    }


def client_snapshot():
    result = {'clients': [], 'distributor_client_id': None, 'error': None}
    try:
        import services
        manager = services.client_manager()
        objects = getattr(manager, '_objects', {})
        for client in list(getattr(objects, 'values', lambda: [])()):
            menu = getattr(client, 'choice_menu', None)
            try:
                menu_count = len(menu) if menu is not None else 0
            except Exception:
                menu_count = None
            result['clients'].append({
                'id': getattr(client, 'id', None),
                'active': getattr(client, 'active', None),
                'active_sim_id': _sim_id(getattr(client, 'active_sim', None)),
                'choice_menu_type': _type_name(menu) if menu is not None else None,
                'choice_menu_revision': getattr(menu, 'revision', None) if menu is not None else None,
                'choice_menu_count': menu_count,
                'interaction_parameters': _primitive(
                    getattr(client, '_interaction_parameters', None)),
            })
        from distributor.system import Distributor
        distributor_client = getattr(Distributor.instance(), 'client', None)
        result['distributor_client_id'] = getattr(distributor_client, 'id', None)
    except Exception as exc:
        result['error'] = '%s:%s' % (type(exc).__name__, exc)
    return result
