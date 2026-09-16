# Build/Buy spike

**PARTIAL — installed build 1.126.78.1020**

The installed game is `D:\Games\The Sims 4`, version `1.126.78.1020` (the
user-data version file has the same value).  Static inspection of
`Data\Simulation\Gameplay\simulation.zip` found `build_buy.pyc` and these
relevant names in its bytecode string table:

| Candidate | Static/live shape | Direction and conclusion |
|---|---|---|
| `register_build_buy_enter_callback` / `register_build_buy_exit_callback` | Python wrappers; live `CallableList` registration; zero-argument callbacks | notification; observes entry/exit only, not a committed wall operation |
| `wall_contour_update_callbacks` | name in `build_buy.pyc` and `zone.pyc`; accessed from current Zone by native fan-out | notification candidate; live Zone attribute and callback arguments still require the real game probe |
| `c_api_wall_contour_update(zone_id, wall_type)` | Python-visible native callable; static body calls current Zone callback list with no args | notification fan-out; not a wall apply function and carries no geometry |
| `get_wall_contours()` | Python-visible native query; callable with zero arguments in the installed build | query; real test returned no ordinary-lot wall geometry before/after a wall edit |
| `get_room_id` / `get_all_block_polygons` / `get_pond_contours_for_wading_depth` | Python-visible native queries; signature not recoverable from the shipped bytecode scan | query; no evidence these represent a committed wall transaction |
| `get_object_placement_flags` / `get_object_buy_category_flags` / `get_object_slotset` | Python-visible native object queries; signature not recoverable from the shipped bytecode scan | unrelated to wall operation capture |
| `begin_update_floor_features` / `end_update_floor_features` / `set_floor_feature` / `remove_floor_feature` | Python-visible native floor-feature surface; signature not recovered | command/apply candidate for floor features only; not evidence of wall edits |
| `c_api_buildbuy_session_begin` / `c_api_buildbuy_session_end` / `c_api_buildbuy_zones_changed` | Python-visible native session/zone notifications | notification; lifecycle/state changes, not a wall payload |
| `c_api_on_lot_clearing_begin/end` / `c_api_on_apply_blueprint_lot_begin/end` | Python-visible native lot-wide operation notifications | notification; too broad and no wall geometry in the static surface |
| `zone._handle_live_drag_objects` / `objects_moved_via_live_drag` / `archive_build` | Python Zone live-drag/build-history names | unrelated or object/live-drag state; no proven wall transaction boundary |
| `placement.*` polygon/footprint helpers | separate `placement.pyc` module | query/placement support; no proven wall-create/delete command |
| `is_in_build_buy` / `get_user_in_build_buy` | Python-visible native mode queries | query; useful guards only |

The enter/exit callbacks are zero-argument `CallableList` notifications in this
build and are now used by KerMP for the fallback build lease UX. They do not
carry wall geometry.

The new `kermp.build.probe` command calls `build_buy.get_wall_contours()` with
no arguments only. It records callable/type/repr/doc/signature and a bounded
normalized return snapshot. A second invocation computes multiset added and
removed contour deltas. No wall capture is emitted from a delta automatically:
the contour structure must first be shown by a live wall edit to contain stable
level/endpoints or another reconstructible identity.

`c_api_wall_contour_update` is a Python callable with static signature
`(zone_id, wall_type)` in the installed bytecode. Its body obtains the current
zone and invokes that zone's `wall_contour_update_callbacks()` with no
arguments; it is a native-to-Python notification fan-out, not a wall apply API.
KerMP now has a guarded `kermp.build.eventprobe` diagnostic that inspects the
live current Zone and registers a callback only when the exposed value is an
explicitly appendable/registerable callback collection. The callback logs its
actual `*args/**kwargs` and event count; KerMP never invokes the native fan-out
manually.

The installed archive contains 2,965 `.pyc` entries. Focused reconnaissance of
`build_buy.pyc`, `zone.pyc`, `placement.pyc`, and related build/lot modules found
no Python-visible request/transaction/undo/redo wall command carrying endpoint
coordinates. The strongest static blocker is therefore: if the live Zone
callback exists and fires, it is currently only a commit/change notification;
the geometry-producing operation appears to remain in native Build/Buy code.

No proven Python callable for applying a wall was found.  The names above are
not treated as an API contract until exercised in the live embedded runtime.
No runtime tracing has yet been performed because KerMP is not installed as a
loaded `.ts4script` and Python 3.7 is absent on this machine.

KerMP now has a tested, game-agnostic Sims adapter with normalization,
duplicate suppression, remote-apply echo suppression, `kermp.build.status`,
and `kermp.build.replay_last`.  It intentionally reports capture/apply as
unavailable until a real callback is configured; it does not create visual
fake walls or claim live capture.
