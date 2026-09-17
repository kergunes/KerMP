# Current Sims interaction pipeline investigation

## Scope and evidence baseline

- KerMP baseline: `2219bbfd262ccf76122b5e8b23d911ae864522a7`.
- Installed game: The Sims 4 `1.126.78.1020` (`Default.ini`, code changelist
  `12042370`).
- Current Python surface inspected directly from
  `Data/Simulation/Gameplay/{simulation,core}.zip`.
- `simulation.zip` SHA-256:
  `5F9DBF9B647EEDE80FCB0FC8330107594251DD78B54E4FF9C103015AAF791A8C`.
- Historical reference: `princess2010/ts4multiplayer`, branch `alpha1`, commit
  `992ff752deb83edb7a2ddebd442e3ae0e2795b86`.
- The active Mods tree was checked before investigation. No S4MP or SimSync
  script/package was present; KerMP was the only multiplayer-control mod.

Labels in this document are intentionally strict:

- **CONFIRMED**: directly observed live, or unambiguous current-build bytecode.
- **STRONG INFERENCE**: multiple current facts support it, but the decisive live
  trace is not captured yet.
- **UNPROVEN**: requires the bounded runtime experiment below.
- **REJECTED**: contradicted by current-build code or runtime evidence.

## Current-build static call graph

### Terrain hover/click eligibility

**CONFIRMED (live + current bytecode)**

`interactions.has_choices` is registered as a Live command and resolves to
`server_commands.interaction_commands.has_choices(...)`. It:

1. resolves `Client` with `services.client_manager().get(_connection)`;
2. resolves that client's active Sim;
3. creates a native `PickInfo` and interaction context;
4. tests potential AOPs through `ChoiceMenu.is_valid_aop`;
5. sends `MSG_OBJECT_IS_INTERACTABLE`.

The existing clean client evidence (`last_command=interactions.has_choices`) is
therefore real terrain/UI boundary evidence, not a guessed Sim API.

### Pie-menu construction

**CONFIRMED (current bytecode)**

`interactions.choices` is registered to
`server_commands.interaction_commands.generate_choices(...)`. The current
terrain branch performs:

```text
generate_choices(connection, target/pick coordinates...)
  -> client = ClientManager.get(connection)
  -> sim = active Sim for that Client
  -> choice_menu = ChoiceMenu(sim)
  -> PickInfo(current terrain/object/Sim pick)
  -> client.create_interaction_context(sim, pick=pick)
  -> target.potential_interactions(context)
  -> choice_menu.add_potential_aops(...)
  -> client.set_choices(choice_menu)
  -> create_pie_menu_message(...)
  -> Distributor.add_event(MSG_PIE_MENU_CREATE, message)
```

`Client.set_choices` stores the menu in `Client._choice_menu`. Choice IDs and
the menu revision belong to that specific `ChoiceMenu` instance.

### Choice selection and queue insertion

**CONFIRMED (current bytecode, conditional on entry)**

The current build still registers `interactions.select` to
`select_choice(choice_id, reference_id=0, _connection=None)`. If that function
is invoked, the exact Python chain is:

```text
interaction_commands.select_choice(choice_id, reference_id, connection)
  -> ClientManager.get(connection)
  -> Client.select_interaction(choice_id, reference_id)
     -> require client.choice_menu is not None
     -> require reference_id == client.choice_menu.revision
     -> clear client._choice_menu
     -> ChoiceMenu.select(choice_id)
        -> selection.aop.test_and_execute(selection.context)
           -> AOP.test(context)
           -> AOP.execute(context)
              -> AOP.interaction_factory(context)
              -> AOP.execute_interaction(interaction)
                 -> context.sim.queue.append(interaction) [non-immediate]
```

`Sim.push_super_affordance` is a parallel programmatic entry that constructs an
AOP and calls `test_and_execute`; it is not required by `ChoiceMenu.select`.

**CONFIRMED:** queue insertion for a normal non-immediate interaction is
synchronous inside `AOP.execute_interaction`. Timeline work is needed later to
transition/run the queued interaction, not to dispatch the selection or append
it to the queue.

**UNPROVEN:** whether the current native UI invokes the registered
`interactions.select` closure, calls `Client.select_interaction` directly, or
stops before both on the failing KerMP client. The passive tracer exists to
answer exactly this without selecting the answer in advance.

## Why the previous telemetry was misleading

**CONFIRMED (KerMP code)**

The host replay path previously forced `result = True` for `has_choices`,
`choices`, `select`, and cancel commands and then emitted
`interaction.started` for every truthy replay. This exactly explains a status
such as `started=14` while no Sim physically began an interaction.

Telemetry is now separated into:

- command captured/received/replayed;
- command result truthy;
- actual interaction present in `sim.queue`;
- actual EA `_trigger_interaction_start_event`.

Only the last signal emits `interaction.started`. Queue membership emits the
new `interaction.queued` signal. Per-command counts prevent a later
`has_choices` hover from hiding an earlier `choices` or `select` call behind one
`last_command` field.

## Remote headless Client model

**CONFIRMED (current bytecode + KerMP code)**

- `ClientManager.create_client` constructs a normal `Client` and calls manager
  `add`; the `Client` itself owns `_choice_menu`, interaction parameters, and an
  active Sim reference.
- KerMP's remote Client remains in `ClientManager`, has an independent active
  Sim, and intentionally skips stock `Client.on_add` Distributor registration.
- The stock current `Distributor` supports one `client`; `add_client` raises if
  another is already registered.
- `generate_choices(remote_connection)` can therefore populate the remote
  Client's private `ChoiceMenu` without registering it in Distributor.

**STRONG INFERENCE:** this is sufficient for host-side selection execution only
if the client's `(choice_id, revision)` came from that exact host-side menu.
KerMP currently creates one menu locally and independently creates another on
the host. Equality of choice IDs/revisions across processes is not guaranteed
and must not be an architectural assumption.

**CONFIRMED:** current `generate_choices` sends `MSG_PIE_MENU_CREATE` through
the singleton Distributor, not through `remote_client.send_message`. KerMP also
classifies that message as local-only, so the normal host output replication
does not return the host menu to the remote UI. The current headless Client is
therefore enough to store menu state, but not by itself enough to route the
matching menu response to the initiating player.

This does not yet justify a custom Distributor. A minimal, scoped routing layer
for the command-correlated local-only response may be sufficient, but it should
be implemented only after the client selection trace is captured.

## Historical S4MP comparison

**CONFIRMED (historical source)**

The alpha implementation did more than forward `interactions.select`:

- On the multiplayer client it re-registered the whole EA command set
  (`has_choices`, `choices`, `select`, push/cancel, clock, active Sim, and UI
  responses) with a wrapper that serialized arguments and did not execute the
  authoritative handler locally.
- On the host it created a stand-in `Client` with its own active Sim and called
  the original EA command functions using that Client's connection ID.
- It marked `has_choices` and `generate_choices` as pending client commands.
  `MSG_OBJECT_IS_INTERACTABLE` and `MSG_PIE_MENU_CREATE` were then routed back
  specifically to the Client that initiated the command.
- Its `SystemDistributor` held one Distributor per Client. General events and
  view updates could broadcast, while pending pie/interactable responses and
  Sim-owned dialogs were client-specific.

The stand-in Client was necessary because current EA command handlers resolve
active Sim and private `ChoiceMenu` state from `_connection`. The targeted menu
response was equally important: it made the remote UI display the host-created
menu whose choice IDs/revision the host later selected.

Still applicable:

- intercept at native player-command/UI-response boundaries;
- execute original EA handlers on the authoritative host;
- maintain one real EA `Client` context per remote player;
- preserve command ownership for client-specific replies;
- broadcast authoritative simulation state, but target private UI replies.

Not applicable without redesign:

- `repr`/comma argument serialization;
- hard-coded client/account IDs;
- old function signatures;
- replacing the entire current Distributor;
- assuming the alpha source describes commercial S4MP 2026.9.

## Hypothesis ledger

| Hypothesis | State | Evidence |
|---|---|---|
| H1 current UI does not use `interactions.select` | **UNPROVEN** | Current command still exists; live per-command trace was previously absent. |
| H2 KerMP registry wrapper is bypassed | **UNPROVEN** | `has_choices` proves at least one replacement works; registry dump now records bounded command descriptions and handler identity. |
| H3 an original `select_choice` reference was cached | **UNPROVEN / weak** | Current Python bytecode has no caller other than command registration; a native cached callable remains possible. |
| H4 selection uses protobuf/omega/UI response | **UNPROVEN** | `omega` is server-to-UI sending in current Python; no Python protobuf selection handler was found. Native dispatch remains possible. |
| H5 terrain Go Here bypasses `ChoiceMenu.select` | **REJECTED if `Client.select_interaction` fires** | Current `Client.select_interaction` has no terrain special case and always calls `ChoiceMenu.select` after revision validation. |
| H6 KerMP command proxy disrupts menu state | **STRONG INFERENCE** | Local and host menus are independently generated; host local-only menu response is not routed to the remote UI. |
| H7 Timeline suppression prevents selection dispatch | **REJECTED for synchronous dispatch/queue append; UNPROVEN for later UI servicing** | The current selection-to-queue chain is synchronous. Timeline is required to run the queued interaction. |
| H8 historical command boundary changed | **UNPROVEN** | Names/signatures still exist, but native UI dispatch behavior needs the live trace. |

## Passive tracer

The development-only tracer is inert until explicitly enabled. It records a
bounded ring of entry/exit/error events for:

- current interaction command handlers;
- `Client.set_choices` / `Client.select_interaction` / `Client.send_message`;
- `ChoiceMenu` construction, population, selection, and clear;
- AOP test/factory/execute/test-and-execute;
- `Sim.push_super_affordance`;
- queue append/remove/process/run;
- `sims4.commands.execute`, `omega.send`, selected Distributor/UI boundaries;
- a sampled `Timeline.simulate` signal.

Each event contains sequence, monotonic time, module/function, thread, role,
connection/client/active-Sim IDs, menu revision/count, bounded primitive
arguments, return type, and bounded exception details. Game objects are never
recursively repr'd. `kermp.trace.stop` writes JSONL under the Sims user data
`KerMP` folder.

Commands:

```text
kermp.trace.registry
kermp.trace.clients
kermp.trace.start
kermp.trace.mark user_selection   # optional
kermp.trace.stop
kermp.trace.status
kermp.trace.dump 300
```

## One decisive next runtime experiment

Use KerMP only; restart Sims fully after installing the development build.

1. On CLIENT run `kermp.trace.registry`, `kermp.trace.clients`, then
   `kermp.trace.start`.
2. Click empty terrain, wait for the menu, click **Go Here**, wait two seconds.
3. Run `kermp.trace.stop`, then `kermp.play.status` and
   `kermp.trace.clients`.
4. Preserve `KerMP/interaction-trace-session-1.jsonl` from the CLIENT.
5. If possible, run the same trace on HOST simultaneously and preserve its
   file; that additionally proves remote Client menu state and revision.

Interpretation is exhaustive:

- `interactions.select` wrapper appears first: command capture is the boundary;
  compare local vs host menu revision/choice IDs and fix targeted host menu
  response/state ownership.
- no command wrapper, but `Client.select_interaction` appears: hook that direct
  current-build boundary and carry the exact choice/revision intent.
- neither appears, but a UI/protobuf/native boundary appears: proxy that proven
  boundary.
- `Client.select_interaction` appears then exits without `ChoiceMenu.select`:
  menu is missing or revision mismatched; recorded revisions identify which.
- `ChoiceMenu.select` and AOP appear but queue append does not: the failure is
  AOP test/execute, not UI dispatch.
- queue append appears but start does not: capture works; investigate host
  Timeline/routing execution separately.
- client Timeline events are suppressed while selection and queue calls still
  occur: suppression is not the dispatch blocker.

No final capture-boundary implementation should be selected before this trace.
