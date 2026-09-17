"""Pure helpers for transactional in-process module reloads.

No Sims modules are imported here so the transaction primitive can be unit
tested outside the game.
"""
from __future__ import print_function

import hashlib


_PRESERVE_DUNDERS = (
    '__name__', '__file__', '__package__', '__loader__', '__spec__',
    '__builtins__', '__doc__',
)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compile_file(path, module_name):
    with open(path, 'r', encoding='utf-8') as handle:
        source = handle.read()
    return compile(source, path, 'exec')


def snapshot_module(module):
    return dict(module.__dict__)


def restore_module(module, snapshot):
    namespace = module.__dict__
    namespace.clear()
    namespace.update(snapshot)


def exec_module_code(module, code):
    """Execute *code* in the existing module namespace with rollback on error."""
    namespace = module.__dict__
    snapshot = dict(namespace)
    preserved = {}
    for name in _PRESERVE_DUNDERS:
        if name in snapshot:
            preserved[name] = snapshot[name]
    namespace.clear()
    namespace.update(preserved)
    try:
        exec(code, namespace)
    except BaseException:
        namespace.clear()
        namespace.update(snapshot)
        raise
    return snapshot


def wrapper_depth(value):
    depth = 0
    seen = set()
    current = value
    while getattr(current, '_kermp_wrapped', False):
        identity = id(current)
        if identity in seen:
            return 999
        seen.add(identity)
        depth += 1
        current = getattr(current, '_kermp_original', None)
        if current is None:
            break
    return depth
