# Status

## WORKING

- Python standard-library LAN host/client with versioned newline JSON.
- Host-authoritative build lease and idempotent bounded operation history.
- Epoch-based travel barrier with stale/duplicate protection and snapshot hooks.
- Localhost bridge and fake-game integration harness.
- Guarded Sims hot-reload dev mode: SHA-256 source watcher, Python 3.7 build validation,
  atomic source publication, transactional module namespace rollback, bridge dispatch
  freeze/queue, duplicate-wrapper health checks, and explicit restart boundaries.
- CI validates the host test suite and compiles every Sims-side module with Python 3.7.

## PARTIAL

- Sims script adapter and native Build/Buy boundary are prepared but not proven in a live Sims process.
- Hot reload is host/CI verified but still needs one live-Sims acceptance pass for
  EA runtime behavior (`kermp.reload`, `kermp.reload.health`, zone transition, and
  repeated reload stress).
- Reconnect restores a session snapshot; replay of missing operations is bounded by retained history.

## BLOCKED

- Exact Sims Build/Buy wall hook and native application boundary are unknown from supplied static reference artifacts.

## NEXT

- Run the two-PC manual test, then capture one real wall create/delete operation in Sims Build/Buy.
