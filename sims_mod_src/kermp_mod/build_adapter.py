"""Small, game-facing Build/Buy boundary.

This module intentionally contains no guessed Sims calls.  The game adapter can
only report/apply through callables explicitly supplied by a tested hook.
"""
from __future__ import print_function

import json


def normalize_operation(payload):
    payload = payload or {}
    data = payload.get('data') or {}
    op = str(payload.get('op') or '')
    if op not in ('wall.create', 'wall.delete'):
        raise ValueError('unsupported build operation: %s' % op)
    normalized = {'op': op, 'data': dict(data)}
    op_id = payload.get('op_id', payload.get('op_seq'))
    if op_id is not None:
        normalized['op_id'] = str(op_id)
    return normalized


class SimsBuildAdapter(object):
    def __init__(self):
        self._apply = None
        self._capture = None
        self._seen = set()
        self.last_local_operation = None
        self.last_remote_operation = None
        self.applying_remote = False

    def configure(self, capture=None, apply=None):
        self._capture = capture
        self._apply = apply

    def capabilities(self):
        result = set()
        if self._capture:
            result.add('capture')
        if self._apply:
            result.add('apply')
        return result

    def capture_local(self, payload):
        if self.applying_remote:
            return False
        operation = normalize_operation(payload)
        self.last_local_operation = operation
        return operation

    def apply_remote(self, payload):
        operation = normalize_operation(payload)
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
        finally:
            self.applying_remote = False
        return True

    def replay_last(self):
        if not self.last_local_operation:
            return False
        return self.apply_remote(self.last_local_operation)


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
    )
    return {'module_available': True, 'candidates': [name for name in wanted if name in names]}
