"""Small, game-facing Build/Buy boundary.

This module intentionally contains no guessed Sims calls.  The game adapter can
only report/apply through callables explicitly supplied by a tested hook.
"""
from __future__ import print_function

import json
import inspect


def bind_call(original, args, kwargs, positional_names):
    """Bind a Sims call using its real signature, with a verified fallback."""
    try:
        bound = inspect.signature(original).bind_partial(*args, **kwargs)
        result = dict(bound.arguments)
        if 'args' in result:
            result.update(dict(zip(positional_names, result.pop('args'))))
        if 'kwargs' in result:
            result.update(result.pop('kwargs'))
        return result
    except Exception:
        result = dict(zip(positional_names, args))
        result.update(kwargs)
        return result


OBJECT_OPERATIONS = (
    'object.create', 'object.destroy', 'object.move', 'object.definition',
    'object.scale', 'object.set_parent', 'object.clear_parent', 'funds.modify',
    'wall.create', 'wall.delete',
)
OBJECT_ID_OPERATIONS = set(OBJECT_OPERATIONS) - set(('wall.create', 'wall.delete', 'funds.modify'))


def _json_value(value, depth=0):
    """Make a bounded, deterministic representation of game return values."""
    if depth > 5:
        return '<depth-limit>'
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_value(v, depth + 1)
                for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(v, depth + 1) for v in value]
    try:
        attrs = getattr(value, '__dict__', None)
        if attrs:
            return {'__type__': type(value).__name__,
                    'attributes': _json_value(attrs, depth + 1)}
    except Exception:
        pass
    return {'__type__': type(value).__name__, 'repr': repr(value)[:1000]}


def contour_delta(before, after):
    """Compare normalized contour sequences without assuming wall identity."""
    before = list(before or [])
    after = list(after or [])
    before_keys = [json.dumps(item, sort_keys=True, separators=(',', ':'))
                   for item in before]
    after_keys = [json.dumps(item, sort_keys=True, separators=(',', ':'))
                  for item in after]
    before_counts = {}
    after_counts = {}
    for key in before_keys:
        before_counts[key] = before_counts.get(key, 0) + 1
    for key in after_keys:
        after_counts[key] = after_counts.get(key, 0) + 1
    added = []
    removed = []
    for key, count in after_counts.items():
        for _ in range(max(0, count - before_counts.get(key, 0))):
            added.append(after[after_keys.index(key)])
    for key, count in before_counts.items():
        for _ in range(max(0, count - after_counts.get(key, 0))):
            removed.append(before[before_keys.index(key)])
    changed = []
    identity_keys = ('wall_id', 'wallId', 'id', 'guid', 'uid', 'handle')
    before_by_id = {}
    after_by_id = {}
    for item in before:
        if isinstance(item, dict):
            for key in identity_keys:
                if key in item:
                    before_by_id[str(item[key])] = item
                    break
    for item in after:
        if isinstance(item, dict):
            for key in identity_keys:
                if key in item:
                    after_by_id[str(item[key])] = item
                    break
    for identity in sorted(set(before_by_id) & set(after_by_id)):
        if before_by_id[identity] != after_by_id[identity]:
            changed.append({'identity': identity, 'before': before_by_id[identity],
                            'after': after_by_id[identity]})
    return {'before_count': len(before), 'after_count': len(after),
            'added': added, 'removed': removed, 'changed': changed}


def _safe_signature(value):
    try:
        return str(inspect.signature(value))
    except Exception as exc:
        return '<unavailable:%s>' % type(exc).__name__


def probe_wall_contours():
    """Safely call the installed zero-argument wrapper, never with guesses."""
    result = {'module_available': False, 'callable': False, 'type': None,
              'repr': None, 'doc': None, 'signature': None, 'error': None,
              'contours': []}
    try:
        import build_buy
        result['module_available'] = True
        target = getattr(build_buy, 'get_wall_contours', None)
        result['callable'] = bool(callable(target))
        result['type'] = type(target).__name__
        result['repr'] = repr(target)[:1000]
        result['doc'] = (getattr(target, '__doc__', None) or '')[:1000]
        result['signature'] = _safe_signature(target)
        if result['callable']:
            # The live wrapper is declared with *args/**kwargs.  Calling it with
            # no arguments is the only non-destructive invocation we permit.
            raw = target()
            result['raw_type'] = type(raw).__name__
            result['raw_repr'] = repr(raw)[:6000]
            result['contours'] = [_json_value(item) for item in (raw or [])]
    except Exception as exc:
        result['error'] = '%s: %s' % (type(exc).__name__, exc)
    return result


def normalize_operation(payload):
    payload = payload or {}
    data = payload.get('data') or {}
    op = str(payload.get('op') or '')
    if op not in OBJECT_OPERATIONS:
        raise ValueError('unsupported build operation: %s' % op)
    normalized = {'op': op, 'data': dict(data)}
    if op in OBJECT_ID_OPERATIONS and not str(normalized['data'].get('object_id', '')):
        raise ValueError('object_id is required')
    if op == 'object.create' and not str(normalized['data'].get('definition_id', '')):
        raise ValueError('definition_id is required')
    if op == 'funds.modify' and not str(normalized['data'].get('household_id', '')):
        raise ValueError('household_id is required')
    for key in ('object_id', 'parent_id', 'definition_id', 'household_id', 'zone_id'):
        if key in normalized['data'] and normalized['data'][key] is not None:
            normalized['data'][key] = str(normalized['data'][key])
    op_id = payload.get('op_id', payload.get('op_seq'))
    if op_id is not None:
        normalized['op_id'] = str(op_id)
    return normalized


PARENTLESS_PARENT_ID = 0
PARENTLESS_PARENT_TYPE_INFO = (0, 0)
PARENTLESS_SLOT_HASH = 0


def resolve_parent_context(data):
    """Map remote parent fields to the native set_object_location_ex call shape.

    Parentless objects use the root sentinels observed in live native calls:
    parent_id=0, parent_type_info=(0, 0), slot_hash=0.
    """
    data = data or {}
    parent_id = data.get('parent_id')
    if parent_id is None or str(parent_id) in ('', '0'):
        parent_id = PARENTLESS_PARENT_ID
    else:
        try:
            parent_id = int(str(parent_id))
        except (TypeError, ValueError):
            parent_id = PARENTLESS_PARENT_ID
    parent_type_info = data.get('parent_type_info')
    if isinstance(parent_type_info, (list, tuple)):
        try:
            parent_type_info = tuple(int(x) for x in parent_type_info)
        except (TypeError, ValueError):
            parent_type_info = PARENTLESS_PARENT_TYPE_INFO
    else:
        parent_type_info = PARENTLESS_PARENT_TYPE_INFO
    slot_hash = data.get('slot_hash')
    if slot_hash is None or str(slot_hash) in ('', '0'):
        slot_hash = PARENTLESS_SLOT_HASH
    else:
        try:
            slot_hash = int(str(slot_hash))
        except (TypeError, ValueError):
            slot_hash = PARENTLESS_SLOT_HASH
    return parent_id, parent_type_info, slot_hash


class SimsBuildAdapter(object):
    def __init__(self):
        self._apply = None
        self._capture = None
        self._seen = set()
        self.last_local_operation = None
        self.last_operation_by_type = {}
        self.last_remote_operation = None
        self.applying_remote = False
        self.probe_before = None
        self.operation_counts = {}
        self.last_error = None
        self.hooks = {}
        self.captured_total = 0
        self.suppressed_remote_echo = 0
        self.capture_errors = 0
        self.last_capture = None
        self.last_capture_error = None
        self._last_definition = {}
        self._capture_sequence = 0

    def configure(self, capture=None, apply=None):
        self._capture = capture
        self._apply = apply

    def reset_diagnostics(self):
        self.operation_counts = {}
        self.captured_total = 0
        self.suppressed_remote_echo = 0
        self.capture_errors = 0
        self.last_capture = None
        self.last_capture_error = None
        self.last_local_operation = None
        self.last_operation_by_type = {}
        self.last_error = None
        self._last_definition = {}
        self._capture_sequence = 0

    def capabilities(self):
        result = set()
        if self._capture:
            result.add('capture')
        if self._apply:
            result.add('apply')
        return result

    def capture_local(self, payload):
        if self.applying_remote:
            self.suppressed_remote_echo += 1
            return False
        try:
            operation = normalize_operation(payload)
        except Exception as exc:
            self.last_error = '%s: %s' % (type(exc).__name__, exc)
            self.capture_errors += 1
            self.last_capture_error = self.last_error
            return False
        if operation['op'] == 'object.definition':
            object_id = operation['data'].get('object_id')
            definition_id = operation['data'].get('definition_id')
            if self._last_definition.get(object_id) == definition_id:
                return False
            self._last_definition[object_id] = definition_id
        self._capture_sequence += 1
        operation['op_id'] = str(payload.get('op_id') or 'local-%s' % self._capture_sequence)
        self.last_local_operation = operation
        self.last_operation_by_type[operation['op']] = operation
        self.last_capture = operation
        self.captured_total += 1
        self.operation_counts[operation['op']] = self.operation_counts.get(operation['op'], 0) + 1
        return operation

    def apply_remote(self, payload):
        try:
            operation = normalize_operation(payload)
        except Exception as exc:
            self.last_error = '%s: %s' % (type(exc).__name__, exc)
            return False
        key = operation.get('op_id') or json.dumps(operation, sort_keys=True)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.last_remote_operation = operation
        if not self._apply:
            return False
        self.applying_remote = True
        try:
            self._apply(operation)
            self.operation_counts[operation['op']] = self.operation_counts.get(operation['op'], 0) + 1
        except Exception as exc:
            self.last_error = '%s: %s' % (type(exc).__name__, exc)
            raise
        finally:
            self.applying_remote = False
        return True

    def status(self):
        return {
            'capture_hook': bool(self._capture),
            'apply_hook': bool(self._apply),
            'suppression_active': self.applying_remote,
            'last_local_operation': self.last_local_operation,
            'last_remote_operation': self.last_remote_operation,
            'operation_counts': dict(self.operation_counts),
            'hooks': dict(self.hooks),
            'last_error': self.last_error,
            'captured_total': self.captured_total,
            'suppressed_remote_echo': self.suppressed_remote_echo,
            'capture_errors': self.capture_errors,
            'last_capture': self.last_capture,
            'last_capture_error': self.last_capture_error,
        }

    def replay_last(self):
        if not self.last_local_operation:
            return False
        return self.apply_remote(self.last_local_operation)

    def probe(self):
        current = probe_wall_contours()
        if self.probe_before is None:
            self.probe_before = current
            return {'phase': 'baseline', 'snapshot': current}
        delta = contour_delta(self.probe_before['contours'], current['contours'])
        self.probe_before = current
        return {'phase': 'delta', 'snapshot': current, 'delta': delta}


adapter = SimsBuildAdapter()


def build_buy_surface():
    """Return only names present in this installed game build.

    Import is deliberately lazy because the sidecar/test Python must not import
    Sims modules.  Presence is reconnaissance, not proof that a callable can
    observe or mutate a committed wall operation.
    """
    try:
        import build_buy
        names = dir(build_buy)
    except Exception:
        return {'module_available': False, 'candidates': []}
    wanted = (
        'register_build_buy_enter_callback', 'register_build_buy_exit_callback',
        'wall_contour_update_callbacks', 'get_wall_contours',
        'c_api_wall_contour_update', 'is_in_build_buy',
        'c_api_create_object', 'c_api_set_object_location_ex',
        'c_api_modify_household_funds', 'c_api_set_parent_object',
        'c_api_clear_parent_object',
    )
    return {'module_available': True, 'candidates': [name for name in wanted if name in names]}
