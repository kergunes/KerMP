"""Lifecycle-safe client simulation suppression for KerMP.

The Sims 4 needs ``Timeline.simulate`` while a save/zone is loading and while
travel/shutdown transitions are in progress.  Suppressing it merely because the
sidecar already identified this process as a client can deadlock those lifecycle
transitions.  S4MP's override model also exempts traveling from its client
Timeline override; use the game's own ``game_services.service_manager`` signal
instead of relying only on KerMP's travel transaction flag.

Python 3.7 compatible: this module is packaged into ``KerMP.ts4script``.
"""


def game_lifecycle_bypass():
    """Return True while Sims must retain its original Timeline simulation.

    The explicit ``is_traveling`` signal is the primary boundary.  The live
    zone/client/active-Sim checks cover initial save loading, main-menu state,
    and late shutdown windows where the sidecar may remain connected while the
    gameplay client is not ready.  Lifecycle detection deliberately fails open:
    a transient unknown state is safer than deadlocking load/exit.
    """
    try:
        import game_services
        manager = getattr(game_services, 'service_manager', None)
        if manager is not None:
            traveling = getattr(manager, 'is_traveling', False)
            if callable(traveling):
                traveling = traveling()
            if bool(traveling):
                return True
    except Exception:
        pass

    try:
        import services
        zone = services.current_zone()
        if zone is None:
            return True
        client = services.get_first_client()
        if client is None:
            return True
        if getattr(client, 'active_sim_info', None) is None:
            return True
    except Exception:
        return True
    return False


def install(hooks_module=None):
    """Replace hooks' Timeline installer with a lifecycle-aware implementation.

    The replacement still records the Timeline patch through hooks._record_patch,
    so normal KerMP teardown/hot reload restores the real Sims method.
    """
    if hooks_module is None:
        from . import hooks as hooks_module

    current = getattr(hooks_module, '_install_timeline_suppression', None)
    if getattr(current, '_kermp_lifecycle_guarded', False):
        return True

    def install_timeline_suppression():
        if getattr(hooks_module, '_timeline_suppression_installed', False):
            return True
        try:
            import scheduling
            timeline = getattr(scheduling, 'Timeline', None)
            if timeline is None:
                hooks_module._simulation_status['last_error'] = 'timeline_missing'
                return False
            original = getattr(timeline, 'simulate', None)
            if not callable(original):
                hooks_module._simulation_status['last_error'] = 'timeline_simulate_missing'
                return False

            # A stale KerMP wrapper should not become the new base implementation.
            # Normal teardown removes it, but this fallback keeps a reconnect/race
            # from stacking suppression wrappers.
            if getattr(original, '_kermp_wrapped', False):
                base = getattr(original, '_kermp_original', None)
                if callable(base):
                    original = base

            def wrapped(*args, **kwargs):
                lifecycle = game_lifecycle_bypass()
                manual = bool(getattr(hooks_module, '_simulation_bypass', False))
                bypass = bool(lifecycle or manual)
                try:
                    hooks_module._simulation_status['bypass'] = bypass
                    hooks_module._simulation_status['lifecycle_bypass'] = bool(lifecycle)
                except Exception:
                    pass
                if getattr(hooks_module, '_sidecar_role', None) == 'client' and not bypass:
                    return None
                return original(*args, **kwargs)

            wrapped._kermp_wrapped = True
            wrapped._kermp_lifecycle_guarded = True
            wrapped._kermp_original = original
            timeline.simulate = wrapped
            hooks_module._record_patch(timeline, 'simulate', original)
            hooks_module._timeline_suppression_installed = True
            hooks_module._simulation_status.update({
                'timeline_suppression_available': True,
                'installed': True,
                'local_simulation_enabled': False,
                'bypass': bool(game_lifecycle_bypass() or getattr(hooks_module, '_simulation_bypass', False)),
                'lifecycle_bypass': bool(game_lifecycle_bypass()),
                'last_error': None,
            })
            try:
                hooks_module._log('KERMP lifecycle-safe simulation suppression installed (client)')
            except Exception:
                pass
            return True
        except Exception as exc:
            hooks_module._simulation_status['last_error'] = 'timeline_install:%s: %s' % (type(exc).__name__, exc)
            return False

    install_timeline_suppression._kermp_lifecycle_guarded = True
    hooks_module._install_timeline_suppression = install_timeline_suppression
    return True
