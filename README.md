# KerMP — LAN multiplayer sidecar and Build/Buy foundations

## Windows GUI

The normal startup path is now the real desktop application (no terminal is
required):

```powershell
python -m kermp.gui
# or scripts\\gui.bat
```

Choose **Host game** or **Join game**. The GUI starts the actual
`HostRuntime`/`ClientRuntime` in a background asyncio thread, shows the LAN
address, player/session state, bridge waiting state, and keeps a small local
JSON preference file at `%LOCALAPPDATA%\\KerMP\\gui.json`. Stop/Disconnect
closes the bridge and network sockets. Windows Firewall may need to allow
Python/KerMP on Private networks; the GUI does not modify firewall rules.

The GUI's **Build / Install Sims Mod** action invokes the existing packaging
script. The destination is derived from `Path.home()` or the optional
`KERMP_SIM_USER_DIR` environment variable; no developer-specific path is
required.

## Build/Buy object protocol

The authoritative build stream now validates and sequences these normalized
operations: `object.create`, `object.destroy`, `object.move`,
`object.definition`, `object.scale`, `object.set_parent`,
`object.clear_parent`, and `funds.modify`. IDs are transported as JSON-safe
strings at the Sims adapter boundary, remote application is duplicate-safe and
suppressed from recapture, and the global build lease remains the v0.1
authority model.

The Sims script performs current-build symbol reconnaissance for the object
create/move/destroy/definition/scale/parent/funds boundaries and fails closed
when a signature is not known. `kermp.build.status` reports these capabilities.
Natural Build/Buy capture still needs live verification against the installed
Sims build; this session has no live Sims available, so no runtime acceptance is
claimed. The client-side ViewUpdate apply now prefers the global
`omega.send(client.id, msg_id, raw)` path and retains the old client-bound path
only as a compatibility fallback.

## REAL SIMS BUILD TEST

The current Sims-side adapter exposes the diagnostic and replay boundary, but it
does not claim a wall hook until a tested game callback is supplied.  In-game:

```text
kermp.status
kermp.ping
kermp.build.status
kermp.build.probe
kermp.native.status
kermp.native.take
```

After a real committed wall hook reports `capture_available=True`, draw one
wall, confirm `KERMP BUILD CAPTURE type=wall.create` in the log, then run:

```text
kermp.build.replay_last
```

Replay enters the same `build.apply` adapter and has duplicate/echo suppression.
`apply_available=False` is an explicit native-boundary blocker, not a visual
fake-wall success.

For the live contour probe, run `kermp.build.probe`, draw exactly one wall in
Build Mode, then run `kermp.build.probe` again. The console reports before/after
counts and added/removed/changed counts; `Documents\Electronic Arts\The Sims 4`
logs contain the bounded raw representation and delta.

Build/Buy enter/exit callbacks are used for the fallback lease UX. They acquire
on entering Build Mode and release on exit; they do not imply wall capture.

Today's target is intentionally narrow: prove the LAN/session/event architecture before spending time on Sims-native hooks.

## Implemented now

- persistent local UUID identity; no cloud/auth dependency
- host-authoritative TCP LAN transport
- protocol versioning + newline JSON envelopes
- duplicate player-id rejection
- ordered host build stream
- global leased Build authority (safe first implementation)
- normalized `wall.create` / `wall.delete` messages
- travel barrier state machine (ready → commit → all-zone-ready → resume)
- localhost game/sidecar bridge
- source-only Sims script bridge skeleton
- automated tests

## Not implemented yet — today's hard blockers

1. Bind the Sims 4 current scripting runtime to emit/receive travel lifecycle events.
2. Find a stable Build/Buy wall operation capture/apply point in the current game build.
3. Compile/package the Sims script with the exact Python runtime expected by the installed Sims 4 build.

The wall hook is the go/no-go item. Do **not** expand scope into CAS, independent lots, dedicated servers, or general multiplayer before that works.

## Run the LAN skeleton now

Requires Python 3.10+ on both PCs.

Host:

```powershell
python -m kermp.cli host --name Kerem
```

Client:

```powershell
python -m kermp.cli join 192.168.1.10 --name Player2
```

Or use `scripts\host.bat` and `scripts\join.bat`.

On the client:

```text
lock
wall 0 0 6 0 0
wall 6 0 6 4 0
unlock
```

The host and client should both see ordered `build.apply` events.

## Architecture

```text
The Sims 4                  The Sims 4
   |                           |
KerMP.ts4script          KerMP.ts4script
   | localhost                 | localhost
KerMP sidecar  <---- LAN ----> KerMP sidecar
       HOST authoritative
```

The game-facing bridge is intentionally localhost-only. LAN details never need to live inside the Sims process.

## Build synchronization rule for v0.1

Do not attempt true simultaneous Build Mode yet. One global lease owns Build authority. This is deliberately crude and removes most conflict classes while wall replication is proven.

## Travel synchronization rule for v0.1

Travel is a barrier operation. Every participant must acknowledge readiness, load the same target zone, and report zone-ready before simulation resumes.

## Next implementation order

1. Current Sims scripting toolchain and a minimal in-game `game.hello` event.
2. Zone-load callback.
3. Host-triggered travel to a known target zone.
4. Remote client travel + zone-ready barrier.
5. Native Build/Buy reconnaissance.
6. One remote wall-create PoC.
7. Wall delete.
8. Door/window consistency.

Anything else is deferred.

## Sims script packaging note

Current community tooling still targets The Sims 4's embedded Python 3.7 bytecode, and modern game builds require precompiled `.pyc` inside `.ts4script`; raw `.py` is not a reliable install path. `sims_mod_src/build.py` therefore requires Python 3.7 and produces `KerMP.ts4script`.

The in-game source now includes diagnostic commands:

```text
kermp.status
kermp.ping
kermp.travel.request <zone_id>
kermp.wall.test <x1> <y1> <x2> <y2> <level>
```

`kermp.wall.test` only proves game → sidecar → LAN transport. It does not create a wall until the native Build/Buy bridge is implemented.

## End-to-end test without opening Sims

Run host sidecar:

```powershell
python -m kermp.cli host --name Kerem
```

Then in another terminal:

```powershell
python tools/fake_game.py
```

The fake game automatically acknowledges travel barriers. This isolates LAN/sidecar bugs from Sims-hook bugs and makes iteration much faster.
# TONIGHT'S TEST

Local fake-game test:

    scripts\test.bat

Two-PC LAN test (run on the host, then substitute its LAN IP on the client):

    scripts\host.bat Kerem
    scripts\join.bat 192.168.x.x Player2

Package the Sims script by copying `sims_mod_src\kermp_mod` and `build.py` into
the required `.ts4script` archive for the installed game version. Copy that
archive into the Sims `Mods` folder and enable script mods. The current bridge
is localhost-only; it does not make the Sims process a LAN server.

In-game diagnostics: `kermp.status`, `kermp.ping`, `kermp.travel.request`,
`kermp.build.object.status`, `kermp.build.object.reset`, `kermp.wall.test`,
`kermp.native.status`, and `kermp.native.take`. Expected logs include `sidecar.welcome`, `travel.prepare`,

## Core multiplayer vertical slice

## Single-PC real-Sims testing with fake player

`tools\fake_player.py` is a diagnostic remote LAN player. It controls remote
input while the real Sims instance remains the host bridge; `fake_game.py` is a
separate simulated game bridge and should be used for transport-only tests.

Terminal 1:

```powershell
python -m kermp.cli host --name Host
```

In the real Sims instance, launch normally and confirm `kermp.status` reports
`bridge_connected=True`. In Terminal 2:

```powershell
python tools\fake_player.py 127.0.0.1 --player-id player2 --name Player2
```

Useful commands are `select <sim_id>`, `interact <affordance_id> [target_id]`,
`travel <zone_id>`, `status`, `travel-status`, and `raw-status`. Travel remains
a real host barrier request; add `--auto-travel-ack` only when the fake player
is intentionally standing in for a participant. Build diagnostics queue behind
the host build lock (`build-lock`, `move`, `recolor`, `scale`, `sell`, and
`build-release`). `--accept-save-sync` validates chunks into a temporary
directory and never writes the Sims saves directory.

The authoritative host now maintains one `active_sim_id` per player. In a live
zone, run `kermp.sims` in the host Sims instance to enumerate loaded Sims, then
assign the host with `kermp.sim.select <sim_id>`. A diagnostic remote Player 2
can connect with:

```text
python tools/fake_player.py 127.0.0.1 --player-id player2 --name Player2
select <sim_b_id>
interact <interaction_tuning_id> <object_id>
```

The sidecar validates ownership and sends the accepted request to the real Sims
host. The script mod resolves `sim_info_manager().get(sim_id)`,
`object_manager().get(object_id)`, the interaction tuning instance, constructs
an `InteractionContext`, and calls `sim.push_super_affordance(...)`. No movement
or animation is faked. Interaction tuning IDs are build-specific and must be
selected from the installed Sims runtime; the exact live invocation remains a
runtime acceptance item until exercised in the user's current zone.
`travel.commit`, `travel.zone_ready`, `travel.resume`, and `build.apply`.
When broken, collect both sidecar stdout/stderr, the fake-game output, the
player IDs, host/client LAN IPs and ports, and the first protocol error; do not
include account or entitlement data.

## Live interaction and replication diagnostics

In the loaded host Sims zone:

```text
kermp.objects
kermp.affordances <object_id>
kermp.sims
kermp.core.status
kermp.distributor.status
```

`kermp.objects` and `kermp.affordances` use bounded live-manager enumeration;
they do not invent tuning IDs. The sidecar now accepts multiple controllers for
the same Sim, forwards bounded base64 `game.raw_message` packets from host to
clients in order, rejects oversize/stale packets, and buffers client packets
during a travel epoch. Actual Sims Distributor capture/application, natural UI
interaction interception, and real Sims travel execution remain runtime-bound
items until verified against the installed build.
