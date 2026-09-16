# Core multiplayer spike

KerMP's authoritative path is:

`remote player -> sim.select -> HostSession ownership -> interaction.request ->
real Sims host -> sim.push_super_affordance`.

The wire format keeps IDs as strings, so Sims IDs are not narrowed to 32-bit
integers. The host rejects unknown Sims, ownership conflicts, requests for a
different Sim, missing affordances, and duplicate request IDs. Session snapshots
include player active Sim IDs and the current live-Sim list; disconnect removes
the player's controlled-Sim marker.

The Sims-side invocation uses the installed-build API boundary:

* `services.sim_info_manager().get(sim_id)` and `SimInfo.get_sim_instance(...)`
* `services.object_manager().get(object_id)` for an optional target
* `services.get_instance_manager(Types.INTERACTION).get(affordance_id)`
* `InteractionContext(sim, InteractionSource.SCRIPT, Priority.High)`
* `sim.push_super_affordance(affordance, target, context)`

All imports and calls are guarded. Failure emits `interaction.rejected` with a
bounded reason. A truthy returned interaction emits `interaction.started`.
This is intentionally a diagnostic route; tuning IDs and target IDs must come
from the active Sims build and are not guessed or copied from S4MP.

Distributor/ViewUpdate capture is not implemented in this spike. The next safe
step is a bounded observer around a confirmed remote interaction, recording only
operation type, owner/id metadata when exposed, message type, and payload size.
