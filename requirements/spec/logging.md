# Spec: Logging

All log messages use the Python logger hierarchy
(`custom_components.btoddb_room_climate_controller.*`).  Filter by that namespace in HA's
**Settings → System → Logs** to see only RCC messages.  Each message embeds
`[room=<key>]` and/or `[profile=<name>]` tags so you can narrow results with
free-text search.

## Child loggers (per-category filtering)

In addition to the base namespace, RCC emits each category of event under its
own **child logger**, so HA's logger config can set a level per category
independently (and the category shows in the logger-name column, not just in
a message tag):

| Child logger | Covers |
|---|---|
| `custom_components.btoddb_room_climate_controller.sensor` | CC-L1 temperature, CC-L2 humidity, CC-L6 window changes |
| `custom_components.btoddb_room_climate_controller.settings` | CC-L4 toggles, CC-L5 room add/change/remove, CC-L8 target/offset edits |
| `custom_components.btoddb_room_climate_controller.profile` | CC-L3a–j profile events |
| `custom_components.btoddb_room_climate_controller.capabilities` | CC-L10 capability dumps |

The `RCC commanded` line (CC-L7) and the diagnostic exception logs stay on the
base `custom_components.btoddb_room_climate_controller.controller` logger — it's the
device-action line, not a settings/sensor/profile event. Example: to see only
sensor activity, set `custom_components.btoddb_room_climate_controller.sensor: info`
in the HA logger config (and `warning`/`error` to silence the others).

Runtime events (temperature/humidity/window changes, profile actions, toggle
changes, room lifecycle) are emitted at **INFO** level.  Diagnostic detail —
per-step config-flow progress, entity lists registered per platform — is
emitted at **DEBUG** level and requires
`custom_components.btoddb_room_climate_controller: debug` in the HA logger config to
appear.

## Log events

### Temperature and humidity changes (CC-L1, CC-L2)

- **CC-L1** When the controller detects a room's temperature sensor change:
  `[room=<key>] Temperature changed: <old> → <new>°F`
  Logged on the `…btoddb_room_climate_controller.sensor` logger. No predicted command
  list is appended — if the change actually commands a device, that shows up
  as its own `RCC commanded` line (CC-L7), tagged with the triggering reason,
  so the log never reports a command that wasn't actually sent.
- **CC-L2** When the controller detects a room's humidity sensor change:
  `[room=<key>] Humidity changed: <old> → <new>%`
  Also logged on the `…btoddb_room_climate_controller.sensor` logger. Like
  temperature (CC-L1), a humidity change **triggers an evaluation in rooms with
  at least one standalone fan** — there the engine may command fans per
  CC-28..CC-30, and any resulting commands appear on their own `RCC commanded`
  line (CC-L7) with trigger reason `humidity <old>→<new>%`. In a room **without
  fans** the change is still logged but triggers **no** evaluation, since
  humidity is inert there (CC-28). No predicted command list is appended.

Logged by `controller.py` in `_on_change`.  The humidity sensor is tracked
alongside the temperature sensor, and — in rooms with fans — evaluated the same
way.

### Profile events (CC-L3)

Logged on the `…btoddb_room_climate_controller.profile` logger, tagged
`[room=<key> profile=<name>]` (the profile's display name, not its id — more
useful for free-text filtering).

**Lifecycle**
- **CC-L3a** When a profile is applied (scheduled or explicit), including its presets:
  `[room=<key> profile=<name>] Profile '<name>' applied (scheduled|explicit): cooling on@72°F, heating off@68°F`
- **CC-L3b** When a scheduled profile fires but is skipped because manual mode is on:
  `[room=<key> profile=<name>] Profile '<name>' skipped: manual mode active`
- **CC-L3c** When a profile is created via the card:
  `[room=<key> profile=<name>] Profile created: '<name>'`
- **CC-L3d** When a profile is deleted via the card:
  `[room=<key> profile=<name>] Profile deleted: '<name>'`
- **CC-L3e** When a profile is renamed via the card:
  `[room=<key> profile=<new name>] Profile renamed: '<old>' → '<new>'`
- **CC-L3f** When a profile's schedule is enabled or disabled via its switch entity:
  `[room=<key> profile=<name>] Profile schedule enabled|disabled`

**Edits** (any change to a profile's settings)
- **CC-L3g** When a profile preset temperature is changed:
  `[room=<key> profile=<name>] Profile preset edited: <device> target → <N>°F`
- **CC-L3h** When a profile preset use-device toggle is changed:
  `[room=<key> profile=<name>] Profile preset edited: <device> use → on|off`
- **CC-L3i** When a profile's A/C fan-only override preset is changed:
  `[room=<key> profile=<name>] Profile preset edited: fan-only override → on|off`
- **CC-L3j** When a profile's schedule time is changed:
  `[room=<key> profile=<name>] Profile schedule time → HH:MM`

### Toggle changes (CC-L4)

- **CC-L4** When a room's use toggle or manual-mode switch changes:
  `[room=<key>] Toggle '<display name>' → on|off`

Logged on the `…btoddb_room_climate_controller.settings` logger.

Covers: **Use A/C**, **Use heater**, **Use fan**, **Manual mode**,
**A/C fan-only override**, **Heater fan-only override**.

Any device command the toggle causes surfaces separately on the
trigger-annotated `RCC commanded` line (CC-L7), e.g.
`(trigger: switch.office_use_ac changed; ...)`, so the toggle line and the
resulting action correlate without duplicating/predicting the command.

### Room lifecycle (CC-L5)

Logged on the `…btoddb_room_climate_controller.settings` logger.

- **CC-L5a** When a room is first added (integration startup or after config change):
  `[room=<key>] Room created: '<label>': <settings>`
- **CC-L5b** When a room's configuration changes (config flow re-saved):
  `[room=<key>] Room settings changed: '<label>': <settings>`
- **CC-L5c** When a room is removed:
  `[room=<key>] Room removed: '<label>'`

`<settings>` is the room's full configuration — devices, climate/fan/switch
entity ids, temperature/humidity/window sensors, fan-only flags, per-device
limits, and delays — rendered by the pure `describe_room_settings()` helper in
`models.py` so created/changed always show the complete picture. Removal logs
just the label; there is no settings snapshot left to show.

### Window events (CC-L6)

- **CC-L6** When a window sensor transitions to open or closed:
  `[room=<key>] Window <entity_id> opened|closed`

Logged by `controller.py` in `_on_change`, on the `…btoddb_room_climate_controller.sensor`
logger. Only fires when the state value actually changes (`old_state !=
new_state`). Unavailable/unknown states are not treated as open (CC-21), so no
log is emitted for those transitions.

### RCC device actions (CC-L7)

- **CC-L7** When an evaluation produces one or more non-`Delay` device commands:
  `[room=<key>] RCC commanded: <phrase>, <phrase>, ... (trigger: <reason>; <threshold context>)`
  One line per evaluation (not one per command), e.g.
  `[room=office] RCC commanded: A/C → cool, A/C fan speed → high (trigger: temperature 72→73°F; temp 78°F; cooling target 72°F (med 75°F high 78°F))`.
  Each command is rendered as a short device + action phrase (`A/C → cool`,
  `A/C setpoint → 65°F`, `Fan speed → high`, `Fan direction → reverse`,
  `A/C power on`, etc.) so a customer's log shows *why* a device changed without
  needing engine internals.

  The `trigger:` reason identifies what caused this evaluation: a temperature
  change (`temperature <old>→<new>°F`), a humidity change
  (`humidity <old>→<new>%`), a window transition
  (`window <entity_id> opened|closed`), another tracked entity changing
  (`<entity_id> changed` — covers toggles/numbers, e.g. a CC-L4 toggle flip or
  CC-L8 number edit), or `startup` (the initial evaluation run when the
  controller starts, and again after its delayed resubscribe — there is no
  sensor change to attribute it to, but devices may still need commanding to
  reach the desired state).

Logged by `controller.py` in `_run`, at **INFO**, on the base `controller`
logger, after the evaluation's commands have all been attempted, using the
same per-command resolution that sent them — so the logged phrase always
matches what was actually sent, even when an earlier command in the sequence
(e.g. a `SetHvacMode`) changed the device's live range for a later one. The
threshold context lists the room temperature and the target/medium/high
thresholds for each device type the room actually has — the same data the
troubleshooting scenarios in issue #14 need (e.g. "does the fan have the right
thresholds?"). When the room has humidity control (CC-28) and a readable
humidity reading, the context also carries the room humidity and its
target/medium/high thresholds in %, e.g.
`humidity 68% target 60% (med 65% high 70%)`.

### Room target/offset edits (CC-L8)

- **CC-L8** When a room's target temperature or medium/high offset number changes:
  `[room=<key>] <name> → <N><unit>`
  e.g. `[room=office] Cooling target → 72°F`. The room's humidity numbers
  (CC-28) log the same way with their `%` unit, e.g.
  `[room=office] Humidity target → 60%`.

Logged by `number.py` in `RoomNumber.async_set_native_value`, at **INFO** on
the `…btoddb_room_climate_controller.settings` logger, only when the value actually
changed (mirrors the existing `ProfilePresetNumber` pattern).

### Device/fan capability dumps (CC-L10)

- **CC-L10** A one-line capability dump per configured climate/fan entity:
  `[room=<key>] A/C capabilities: <entity_id>: hvac_modes=[...], fan_only=yes|no, fan_modes=[...], min_temp=<N>, max_temp=<N>`
  `[room=<key>] Fan capabilities: <entity_id>: preset_modes=[...], reversible=yes|no, percentage_step=<N>`

Emitted at **INFO** on the `…btoddb_room_climate_controller.capabilities` logger:
- by `controller.py` (`RoomController._log_capabilities`) shortly after the
  controller starts and again after the delayed resubscribe, once entities have
  registered;
- by `config_flow.py` (`async_step_devices`) right after a climate/fan entity is
  selected in room setup — this directly answers "does this A/C have a
  `fan_only` mode?" from the config flow, before the room is even fully set up.

Both call sites share the formatting helpers `describe_climate_capabilities` /
`describe_fan_capabilities` in `entity.py` so the two dumps never drift. An
entity whose state isn't loaded yet logs `<entity_id> (unavailable)` rather than
raising.

### Copy/paste room settings (out of scope)

Copy and paste run entirely in the browser (`card/src/profiles/clipboard.ts`,
`copy-room-settings.ts`) and never call a websocket command, so the integration
cannot log the button presses themselves. This was confirmed out of scope for
issue #14. Their effects are still visible in the log once applied: paste writes
room numbers/switches (CC-L4/CC-L8) or creates a profile (CC-L3c).
