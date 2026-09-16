# Build/Buy spike

**PARTIAL — installed build 1.126.78.1020**

The installed game is `D:\Games\The Sims 4`, version `1.126.78.1020` (the
user-data version file has the same value).  Static inspection of
`Data\Simulation\Gameplay\simulation.zip` found `build_buy.pyc` and these
relevant names in its bytecode string table:

| Candidate | What the name supports | Capture/apply conclusion |
|---|---|---|
| `register_build_buy_enter_callback` / `register_build_buy_exit_callback` | Build/Buy session lifecycle callbacks | observes entry/exit only; not a committed wall operation |
| `wall_contour_update_callbacks` / `c_api_wall_contour_update` | native wall-contour update surface | promising observation boundary; callback signature and commit semantics still unverified |
| `get_wall_contours` | wall geometry query | promising read/query surface; not an apply API |
| `is_in_build_buy` | current mode query | useful guard only |

The enter/exit callbacks are zero-argument `CallableList` notifications in this
build and are now used by KerMP for the fallback build lease UX. They do not
carry wall geometry.

No proven Python callable for applying a wall was found.  The names above are
not treated as an API contract until exercised in the live embedded runtime.
No runtime tracing has yet been performed because KerMP is not installed as a
loaded `.ts4script` and Python 3.7 is absent on this machine.

KerMP now has a tested, game-agnostic Sims adapter with normalization,
duplicate suppression, remote-apply echo suppression, `kermp.build.status`,
and `kermp.build.replay_last`.  It intentionally reports capture/apply as
unavailable until a real callback is configured; it does not create visual
fake walls or claim live capture.
