# Build/Buy object capture reconnaissance

Source inspected: `D:\Games\The Sims 4\Data\Simulation\Gameplay\base.zip` and
`simulation.zip`, current local install. The package is Python 3.7 bytecode.

| Operation | Candidate and observed signature | Implementation status |
|---|---|---|
| Move/rotate | `build_buy.c_api_set_object_location_ex(zone_id, obj_id, routing_surface, transform, parent_id, parent_type_info, slot_hash)`; Python wrapper at line 1418 calls `obj.set_parent(... transform=..., slot_hash=..., routing_surface=...)`. | Wrapper installed. Captures transform, routing surface, parent and slot. Remote calls the same API under suppression. |
| Recolor | `objects.client_object_mixin.ClientObjectMixin.set_definition(self, definition_id, ignore_rig_footprint)`; Python implementation updates the definition and queues `SetObjectDefinitionId`. | Wrapper installed. Duplicate definition changes are ignored. Remote calls the real setter. |
| Destroy/sell | `objects.system.c_api_destroy_object(zone_id, obj_or_id)`; Python wrapper resolves the object and calls `obj.destroy(source='Destruction request from C.', cause=...)`. | Wrapper installed; ID is saved before the original call. Remote calls `c_api_destroy_object`. |
| Create/buy | `objects.system.c_api_create_object(zone_id, def_id, obj_id, obj_state, loc_type, content_source)`; Python wrapper calls `create_object(...)`. | Wrapper installed. Captures returned/explicit ID and definition. Remote uses the same API; exact enum/state reconstruction remains runtime-sensitive. |
| Scale | `ClientObjectMixin._resend_client_scale(self)`; property setter `scale(self, value)` updates `_scale`, calls location notification, then this method. | Wrapper installed at the resend boundary and captures current scale. |
| Funds | `build_buy.c_api_modify_household_funds(amount, household_id, reason, zone_id)`; Python implementation mutates household funds and returns bool. | Wrapper installed and broadcasts observation only. Remote apply deliberately does not mutate funds, preventing double charge/refund. |
| Parent | `objects.system.c_api_set_parent_object(obj_id, parent_id, transform, joint_name, slot_hash, zone_id)` and `c_api_clear_parent_object(obj_id, transform, zone_id, surface)`. | Wrappers installed. Remote calls the same APIs under suppression. |

## Call-site and patch effectiveness

The bytecode scan found the candidate definitions in `build_buy.pyc`,
`objects/system.pyc`, and `objects/client_object_mixin.pyc`. The C API names are
not imported by ordinary Python gameplay call sites in the scanned simulation
package; they are Python-visible wrappers over the native/build-buy boundary.
`set_definition` and scale are Python class methods, and the package call sites
invoke them through the object instance. KerMP patches the defining module/class
before normal gameplay use, preserving the original return value and exception.
Whether the native Build/Buy UI reaches each public Python wrapper still needs
the specified live manual test; this report does not claim runtime acceptance.

## Safety and state

All wrappers call the original first and preserve its return/exception behavior.
Natural capture uses one common adapter boundary, assigns bounded local
operation IDs, records counters, and emits only after success. Remote apply
runs through `adapter.applying_remote`; wrappers then update the game but do not
re-emit. Host funds remain authoritative and are not reapplied by clients.

Routing refresh was not added: the inspected move/create/destroy wrappers did
not establish a safe additional planner API, so KerMP relies on the current
object/build-buy calls until live routing evidence requires a targeted refresh.
