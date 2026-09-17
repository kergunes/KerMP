"""KerMP live-reload controller for The Sims 4.

The controller itself is intentionally stable and is packaged into the normal
.ts4script.  Development source is copied beside the archive under
Mods/KerMP/Scripts/kermp_mod.  Reloads read that source explicitly, so dev mode
does not depend on Python import precedence between loose files and the archive.
"""
from __future__ import print_function

import json
import os
import sys
import traceback

import sims4.commands
import sims4.reload

from . import reload_core
from .runtime_state import runtime, bridge


_STABLE_MODULES = {
    'kermp_mod.__init__',
    'kermp_mod.bridge_client',
    'kermp_mod.runtime_state',
    'kermp_mod.reload_core',
    'kermp_mod.dev_reload',
    'kermp_mod.commands',
    'kermp_mod.build_adapter',
}

_HOOK_STATE = (
    '_pending_travel_txn', '_sidecar_role', '_travel_epoch',
    '_travel_buffering', '_travel_buffer', '_view_updates_received',
    '_view_updates_sent', '_last_view_update_size', '_last_view_update_msg_id',
    '_travel_selected_sim_id', '_travel_batch_complete', '_travel_api_info',
    '_travel_zone_reported', '_travel_local_zone_loaded',
    '_travel_selected_sim_restored', '_travel_last_error',
    '_wall_event_count', '_last_wall_event', '_build_last_error',
    '_simulation_status',
)


def _out(connection):
    return sims4.commands.CheatOutput(connection)


def _find_source_root():
    cursor = os.path.abspath(__file__)
    for _ in range(12):
        cursor = os.path.dirname(cursor)
        candidate = os.path.join(cursor, 'Scripts', 'kermp_mod')
        if os.path.isdir(candidate):
            return candidate
        if not cursor or os.path.dirname(cursor) == cursor:
            break
    return None


def _scripts_root():
    source = _find_source_root()
    return os.path.dirname(source) if source else None


def _json_path(name):
    root = _scripts_root()
    return os.path.join(root, name) if root else None


def _read_json(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_json(path, value):
    if not path:
        return
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True, separators=(',', ':'))
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except Exception:
            pass
    os.replace(temp, path)


def _normalize_module(name):
    name = str(name or '').strip()
    if not name:
        return ''
    if name.endswith('.py'):
        name = name[:-3]
    name = name.replace('/', '.').replace('\\', '.')
    if name == 'hooks':
        return 'kermp_mod.hooks'
    if not name.startswith('kermp_mod.'):
        name = 'kermp_mod.' + name
    return name


def _source_path(module_name):
    root = _find_source_root()
    if not root:
        return None
    prefix = 'kermp_mod.'
    if not module_name.startswith(prefix):
        return None
    relative = module_name[len(prefix):].replace('.', os.sep) + '.py'
    return os.path.join(root, relative)


def _pending_request():
    return _read_json(_json_path('.kermp-reload-request.json'))


def _manifest():
    return _read_json(_json_path('.kermp-dev-manifest.json'))


def _validate_published_generation(request):
    manifest = _manifest()
    request_generation = int(request.get('generation', 0) or 0)
    manifest_generation = int(manifest.get('generation', 0) or 0)
    if request_generation <= 0 or manifest_generation != request_generation:
        raise RuntimeError('generation_manifest_mismatch:request=%s:manifest=%s' %
                           (request_generation, manifest_generation))
    expected = manifest.get('files')
    if not isinstance(expected, dict):
        raise RuntimeError('generation_manifest_files_missing')
    root = _find_source_root()
    if not root:
        raise RuntimeError('dev_source_root_missing')
    actual = {}
    for base, _dirs, files in os.walk(root):
        for filename in files:
            if not filename.endswith('.py'):
                continue
            path = os.path.join(base, filename)
            relative = os.path.relpath(path, root).replace(os.sep, '/')
            actual[relative] = reload_core.sha256_file(path)
    if set(actual) != set(expected):
        raise RuntimeError('generation_file_set_mismatch')
    for relative, digest in expected.items():
        if actual.get(relative) != digest:
            raise RuntimeError('generation_hash_mismatch:%s' % relative)
    return manifest


def _last_ack():
    return _read_json(_json_path('.kermp-reload-ack.json'))


def _sync_in_progress():
    path = _json_path('.kermp-syncing')
    return bool(path and os.path.exists(path))


def _module_reloadable(module_name, module):
    if module_name in _STABLE_MODULES:
        return False
    if module_name == 'kermp_mod.hooks':
        return True
    return bool(getattr(module, '__kermp_hot_reload__', False))


def _reload_order(module_names):
    # Explicit priority keeps boundary modules last.  Future reloadable modules
    # default to lexical order before hooks.
    unique = sorted(set(module_names))
    return sorted(unique, key=lambda name: (100 if name == 'kermp_mod.hooks' else 10, name))


def _prepare(module_name):
    if module_name in _STABLE_MODULES:
        raise RuntimeError('stable_module_requires_restart:%s' % module_name)
    module = sys.modules.get(module_name)
    if module is None:
        raise RuntimeError('module_not_loaded:%s' % module_name)
    if not _module_reloadable(module_name, module):
        raise RuntimeError('module_not_opted_in:%s' % module_name)
    source_path = _source_path(module_name)
    if not source_path or not os.path.isfile(source_path):
        raise RuntimeError('source_missing:%s' % module_name)
    can_reload = getattr(module, 'can_hot_reload', None)
    if callable(can_reload):
        result = can_reload()
        if isinstance(result, dict):
            if not result.get('ok', False):
                raise RuntimeError('unsafe_runtime_state:%s' % (result.get('reason') or result))
        elif isinstance(result, (list, tuple)):
            if not result or not result[0]:
                reason = result[1] if len(result) > 1 else 'unknown'
                raise RuntimeError('unsafe_runtime_state:%s' % reason)
        elif result is False:
            raise RuntimeError('unsafe_runtime_state')
    code = reload_core.compile_file(source_path, module_name)
    snapshot = reload_core.snapshot_module(module)
    preserve = {}
    if module_name == 'kermp_mod.hooks':
        for name in _HOOK_STATE:
            if name in snapshot:
                preserve[name] = snapshot[name]
    return {
        'name': module_name,
        'module': module,
        'source_path': source_path,
        'code': code,
        'snapshot': snapshot,
        'preserve': preserve,
        'sha256': reload_core.sha256_file(source_path),
    }


def _restore_entry(entry):
    module = entry['module']
    reload_core.restore_module(module, entry['snapshot'])
    if entry['name'] == 'kermp_mod.hooks':
        # Reinstall the old generation so refreshed monkey patches point back at
        # the restored code rather than the failed generation.
        try:
            module.__dict__['_installed'] = False
            install = module.__dict__.get('install')
            if callable(install):
                install()
        except Exception:
            pass


def _health(entry):
    module = entry['module']
    health = getattr(module, 'reload_health', None)
    if not callable(health):
        return {'ok': True}
    result = health()
    if isinstance(result, dict):
        return result
    return {'ok': bool(result)}


def _reload_modules(module_names):
    if _sync_in_progress():
        raise RuntimeError('dev_sync_in_progress')
    ordered = _reload_order(module_names)
    prepared = [_prepare(name) for name in ordered]
    if not prepared:
        return {'ok': True, 'modules': [], 'generation': runtime.reload_generation}

    applied = []
    if not bridge.pause_dispatch(5.0):
        bridge.resume_dispatch()
        raise RuntimeError('bridge_dispatch_quiesce_timeout')
    runtime.reload_in_progress = True
    result_payload = None
    try:
        for entry in prepared:
            module = entry['module']
            reload_core.exec_module_code(module, entry['code'])
            # From this point onward every failure must roll this generation
            # back, including failures inside install() or Sims bookkeeping.
            applied.append(entry)
            for name, value in entry['preserve'].items():
                module.__dict__[name] = value
            if entry['name'] == 'kermp_mod.hooks':
                module.__dict__['_installed'] = False
            install = module.__dict__.get('install')
            if callable(install):
                install()
            sims4.reload.update_module_dict(entry['snapshot'], module.__dict__)

        health = {}
        for entry in prepared:
            result = _health(entry)
            health[entry['name']] = result
            if not result.get('ok', False):
                raise RuntimeError('post_reload_health_failed:%s:%s' %
                                   (entry['name'], result.get('reason') or result))

        runtime.reload_generation += 1
        runtime.last_reload = {
            'ok': True,
            'generation': runtime.reload_generation,
            'modules': [entry['name'] for entry in prepared],
            'hashes': {entry['name']: entry['sha256'] for entry in prepared},
            'health': health,
        }
        result_payload = dict(runtime.last_reload)
    except Exception as exc:
        for entry in reversed(applied):
            _restore_entry(entry)
        runtime.last_reload = {
            'ok': False,
            'generation': runtime.reload_generation,
            'modules': [entry['name'] for entry in prepared],
            'error': '%s: %s' % (type(exc).__name__, exc),
            'traceback': traceback.format_exc(),
        }
        raise
    finally:
        runtime.reload_in_progress = False
        resume_errors = bridge.resume_dispatch()
        if resume_errors:
            runtime.last_reload['resume_dispatch_errors'] = resume_errors
            if result_payload is not None:
                result_payload['resume_dispatch_errors'] = resume_errors
    return result_payload


def _write_ack(request, result, restart_required):
    path = _json_path('.kermp-reload-ack.json')
    payload = {
        'request_generation': int(request.get('generation', 0) or 0),
        'runtime_generation': runtime.reload_generation,
        'ok': bool(result.get('ok', False)),
        'modules': list(result.get('modules') or []),
        'restart_required': list(restart_required or []),
        'error': result.get('error'),
    }
    _atomic_json(path, payload)


def _request_modules(request):
    changed = [_normalize_module(item) for item in request.get('modules') or []]
    changed = [item for item in changed if item]
    deleted = [_normalize_module(item) for item in request.get('deleted_modules') or []]
    deleted = [item for item in deleted if item]
    restart = [_normalize_module(item) for item in request.get('restart_required') or []]
    restart = [item for item in restart if item]
    restart.extend(deleted)
    hot = []
    for name in changed:
        if name in _STABLE_MODULES:
            restart.append(name)
        else:
            hot.append(name)
    return _reload_order(hot), sorted(set(restart))


def status():
    source_root = _find_source_root()
    request = _pending_request()
    ack = _last_ack()
    return {
        'dev_source_found': bool(source_root),
        'source_root': source_root,
        'sync_in_progress': _sync_in_progress(),
        'request_generation': int(request.get('generation', 0) or 0),
        'ack_generation': int(ack.get('request_generation', 0) or 0),
        'runtime_generation': runtime.reload_generation,
        'reload_in_progress': runtime.reload_in_progress,
        'last_reload': dict(runtime.last_reload),
        'bridge': bridge.status(),
        'restart_required': list(ack.get('restart_required') or request.get('restart_required') or []),
    }


@sims4.commands.Command('kermp.reload', command_type=sims4.commands.CommandType.Live)
def kermp_reload(module: str = '', _connection=None):
    output = _out(_connection)
    if _sync_in_progress():
        output('KerMP reload refused: dev sync is still writing files.')
        return
    if module:
        names = [_normalize_module(module)]
        restart_required = [name for name in names if name in _STABLE_MODULES]
        names = [name for name in names if name not in _STABLE_MODULES]
        request = {'generation': 0}
    else:
        request = _pending_request()
        if not request:
            output('KerMP reload: no pending dev request.')
            return
        ack = _last_ack()
        request_generation = int(request.get('generation', 0) or 0)
        if request_generation and int(ack.get('request_generation', 0) or 0) >= request_generation:
            output('KerMP reload: request generation %s already processed.' % request_generation)
            if ack.get('restart_required'):
                output('Restart still required for: %s' % ','.join(ack.get('restart_required')))
            return
        try:
            _validate_published_generation(request)
        except Exception as exc:
            output('KerMP reload REFUSED: %s: %s' % (type(exc).__name__, exc))
            output('Published source generation is incomplete or inconsistent; runtime unchanged.')
            return
        names, restart_required = _request_modules(request)

    if not names:
        result = {'ok': True, 'modules': [], 'generation': runtime.reload_generation}
        _write_ack(request, result, restart_required)
        if restart_required:
            output('KerMP: game restart required for %s' % ','.join(restart_required))
        else:
            output('KerMP reload: nothing reloadable changed.')
        return

    try:
        result = _reload_modules(names)
        _write_ack(request, result, restart_required)
        output('KerMP reload OK generation=%s modules=%s' %
               (result.get('generation'), ','.join(result.get('modules') or [])))
        if restart_required:
            output('Game restart also required for: %s' % ','.join(restart_required))
    except Exception as exc:
        result = {'ok': False, 'modules': names, 'error': '%s: %s' % (type(exc).__name__, exc)}
        _write_ack(request, result, restart_required)
        output('KerMP reload FAILED: %s' % result['error'])
        output('Runtime remains on the previous accepted generation; inspect KerMP log before retrying.')


@sims4.commands.Command('kermp.reload.all', command_type=sims4.commands.CommandType.Live)
def kermp_reload_all(_connection=None):
    output = _out(_connection)
    names = []
    for name, module in list(sys.modules.items()):
        if name.startswith('kermp_mod.') and _module_reloadable(name, module):
            names.append(name)
    try:
        result = _reload_modules(names)
        output('KerMP reload-all OK generation=%s modules=%s' %
               (result.get('generation'), ','.join(result.get('modules') or [])))
    except Exception as exc:
        output('KerMP reload-all FAILED: %s: %s' % (type(exc).__name__, exc))


@sims4.commands.Command('kermp.reload.status', command_type=sims4.commands.CommandType.Live)
def kermp_reload_status(_connection=None):
    output = _out(_connection)
    value = status()
    bridge_status = value.get('bridge') or {}
    output('dev_source_found=%s sync_in_progress=%s request_generation=%s ack_generation=%s runtime_generation=%s' %
           (value['dev_source_found'], value['sync_in_progress'], value['request_generation'],
            value['ack_generation'], value['runtime_generation']))
    output('bridge_thread_alive=%s dispatch_paused=%s queued_messages=%s start_count=%s' %
           (bridge_status.get('thread_alive'), bridge_status.get('dispatch_paused'),
            bridge_status.get('queued_messages'), bridge_status.get('start_count')))
    output('source_root=%s' % (value.get('source_root') or 'none'))
    output('restart_required=%s' % (','.join(value.get('restart_required') or []) or 'none'))


@sims4.commands.Command('kermp.reload.health', command_type=sims4.commands.CommandType.Live)
def kermp_reload_health(_connection=None):
    output = _out(_connection)
    module = sys.modules.get('kermp_mod.hooks')
    if module is None:
        output('ok=False reason=hooks_not_loaded')
        return
    health = getattr(module, 'reload_health', None)
    result = health() if callable(health) else {'ok': False, 'reason': 'health_unavailable'}
    output('ok=%s reason=%s details=%s' %
           (result.get('ok'), result.get('reason') or 'none',
            json.dumps(result, sort_keys=True, separators=(',', ':'))))
