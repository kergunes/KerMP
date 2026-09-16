# KerMP P0 core parity boundary

This is an independent LAN sidecar implementation. The supplied reference artifact was used only for clean-room architectural evidence; no reference code, binary, offsets, or authentication behavior is shipped.

## LIVE VERIFIED

- Sims script loading and localhost sidecar communication.
- Host/player Sim assignment and a remote player causing a real host Sims interaction.
- Two players controlling distinct Sims concurrently.
- Host Distributor/ViewUpdate capture, including large volumes of real packets.

## IMPLEMENTED / AUTOMATED TESTED

- Versioned compatibility manifests, strict mismatch reasons, and a per-player readiness model.
- Bounded, ordered save-transfer receiver with SHA-256 verification, partial-file cleanup, atomic finalization, and a timestamped backup of an occupied destination slot.
- Host-owned clock requests/state, whitelisted command correlation, dialog ownership, and audience routing primitives.
- Existing travel barrier, selected Sim routing, raw ViewUpdate limits, Build/Buy primitive replication, and the global Build lease remain in place.
- The native API exposes a bounded, versioned Build/Buy forwarding surface but deliberately refuses forwarding/submission until a current-build hook and main-thread boundary are validated.

Automated evidence: `python -m pytest -q` passes 35 tests. Sims source compatibility: Python 3.7 bytecode compilation passes.

## RUNTIME UNVERIFIED

- Real two-machine save transfer followed by safe Sims slot loading. The transfer API is implemented; automatic Sims save-slot loading is not guessed.
- Current-build client simulation suppression. The script detects the Timeline surface but does not monkey patch it without a live, presentation-safe validation.
- Natural pie-menu choice interception, dialog rendering/callback continuation, normal travel UI interception, targeted live Distributor classification, and host-side native Build/Buy submission/result injection.
- Native object request interception before client simulation. The API fails closed instead of intercepting an unknown game boundary.

## BLOCKED

- A legitimate current-Sims runtime capture is required to establish safe native Build/Buy interception/submission and a main/UI-thread dispatch boundary.
- A current-Sims runtime capture is required to prove client Timeline suppression does not stop rendering, UI, zone loading, or Distributor application.

## Manual acceptance plan

1. **Compatibility:** launch Host and Join GUI on two machines; confirm same detected game/mod versions and enabled packs, then deliberately alter one manifest field and confirm an understandable refusal.
2. **Save sync:** use a deliberately different client slot, select an explicit host slot, join, verify bytes/progress and matching SHA-256, then confirm the timestamped client backup.
3. **Save load:** only after a current-build Sims load boundary is validated, verify the exact synchronized slot is loaded on the client.
4. **Authority:** confirm host clock changes replicate and client simulation status remains fail-closed until suppression has live evidence.
5. **Travel/interaction/BuildBuy:** exercise normal Sims UI with host and client logs; classify each as live-verified only after observing host execution and correct client presentation.
