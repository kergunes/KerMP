# KerMP fast-start — v0.0.1

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
`kermp.wall.test`. Expected logs include `sidecar.welcome`, `travel.prepare`,
`travel.commit`, `travel.zone_ready`, `travel.resume`, and `build.apply`.
When broken, collect both sidecar stdout/stderr, the fake-game output, the
player IDs, host/client LAN IPs and ports, and the first protocol error; do not
include account or entitlement data.
