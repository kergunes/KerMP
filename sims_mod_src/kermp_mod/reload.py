"""In-game hot reload for KerMP development.

Requires a dev-mode install (``python sims_mod_src/build.py --dev``): loose
``.py`` source under ``Mods/KerMP/Scripts/kermp_mod``. Restart the game once so
that source loads, then edit ``kermp_mod`` and run ``kermp.reload`` in the cheat
console instead of restarting the game.

The compiled ``.ts4script`` has no reloadable source, so ``kermp.reload`` only
works with the dev-mode install.
"""
import os
import sys

import sims4.commands

_RELOAD_ORDER = ('bridge_client', 'build_adapter', 'hooks', 'commands')


def _out(connection):
    return sims4.commands.CheatOutput(connection)


def _package_dir(hooks_module):
    return os.path.dirname(os.path.realpath(hooks_module.__file__))


@sims4.commands.Command('kermp.reload', command_type=sims4.commands.CommandType.Live)
def kermp_reload(_connection=None):
    out = _out(_connection)
    from . import hooks
    hooks_name = getattr(hooks, '__name__', 'kermp_mod.hooks')
    package_dir = _package_dir(hooks)
    for name in _RELOAD_ORDER:
        if not os.path.isfile(os.path.join(package_dir, name + '.py')):
            out('dev source missing for %s (run build.py --dev first)' % name)
            return
    try:
        teardown_errors = hooks.teardown()
    except Exception as exc:
        teardown_errors = ['teardown raised: %s' % exc]
    if teardown_errors:
        for error in teardown_errors:
            out('teardown error: %s' % error)
        out('reload aborted: teardown incomplete')
        return
    import sims4.reload as reload_service
    reload_fn = (getattr(reload_service, 'reload_file', None)
                 or getattr(reload_service, 'reload', None))
    if not callable(reload_fn):
        out('sims4.reload unavailable in this build')
        return
    for name in _RELOAD_ORDER:
        path = os.path.join(package_dir, name + '.py')
        try:
            reload_fn(path)
            out('reloaded %s' % name)
        except Exception as exc:
            out('reload failed %s: %s' % (name, exc))
            return
    hooks_mod = sys.modules.get(hooks_name) or hooks
    try:
        hooks_mod.install()
        out('KerMP reinstalled')
    except Exception as exc:
        out('install failed: %s' % exc)
