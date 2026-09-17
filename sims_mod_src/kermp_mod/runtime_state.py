"""Persistent state that must survive KerMP hot reloads.

This module is deliberately excluded from live reload.  Reloadable game modules
may be re-executed in place, but the bridge connection, callback registrations,
and reload coordination state must retain identity across generations.
"""
from __future__ import print_function

import sys
import threading

from .bridge_client import KerMPBridgeClient


class RuntimeState(object):
    def __init__(self):
        self.bridge = KerMPBridgeClient()
        self.registrations = {}
        self.reload_generation = 0
        self.reload_in_progress = False
        self.last_reload = {}
        self.reload_lock = threading.RLock()


runtime = RuntimeState()
bridge = runtime.bridge

_HOOKS_MODULE = (__package__ or 'kermp_mod') + '.hooks'


def _dispatch(function_name, *args, **kwargs):
    module = sys.modules.get(_HOOKS_MODULE)
    if module is None:
        return None
    function = getattr(module, function_name, None)
    if not callable(function):
        return None
    return function(*args, **kwargs)


def build_buy_enter_dispatch(*args, **kwargs):
    return _dispatch('_on_build_buy_enter', *args, **kwargs)


def build_buy_exit_dispatch(*args, **kwargs):
    return _dispatch('_on_build_buy_exit', *args, **kwargs)


def wall_contour_dispatch(*args, **kwargs):
    return _dispatch('_wall_contour_update_callback', *args, **kwargs)
