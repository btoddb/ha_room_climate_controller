# Spec: Per-room climate control

The integration maintains each room's target temperature (and drives fan speed)
by reactively controlling that room's real `climate` / `fan` / `switch` devices.
The decision logic is a **pure function**,
[`compute_commands(inputs)`](../../custom_components/btoddb_room_climate_controller/engine.py);
[`controller.py`](../../custom_components/btoddb_room_climate_controller/controller.py)
gathers live state, calls it, and executes the returned commands. Manual-mode
gating is the controller's job — the engine assumes control is active.

## Devices & wiring

A room has up to four **device types**, in canonical order: **cooling** (A/C),
**heating** (heater), **fan** (standalone), **vent fan** (CC-36). A room only has
the devices it's configured with; absent devices are ignored in all rules,
entities, and the card. A room may also have optional **window sensors** that
suppress conditioning while any is open (CC-20).

- **CC-1** Each device type has an independent **Use** toggle. Use on → the engine may drive that device. Use off → the device is turned off unless another rule supersedes it (see fan-only override).
- **CC-2** A **combined** room wires one `climate` entity to *both* cooling and heating (a heat pump). It's modeled by `combined=True` with `ac_climate == heater_climate`; the engine runs the combined branch instead of separate A/C + heater branches.
- **CC-3** A climate device is **fan-capable** if its `hvac_modes` include `fan_only` or it exposes any `fan_modes`. Fan speed is driven either via the climate's own `fan_mode` or, failing that, a configured **companion fan** entity (`ac_fan_entity` / `heater_fan_entity`).
- **CC-4** A device may have an optional **power switch** (`ac_power_switch` / `heater_power_switch`). The engine powers it on (then waits `power_on_delay`) before driving the climate, and powers it off when the decision is OFF.

## Per-room controls (entities)

For each device the room has, the integration creates live entities the engine
reads and the card/profiles write. A room may have **multiple standalone fans**
(`fan_entities`, a list); each fan's live entities are keyed by a **slug of the
fan's source entity id**, so per-fan keys take the form `…__<slug>`.

- **Target temp** — `number.*` (`target_cooling_temp` / `target_heating_temp`; per fan, `target_fan_temp__<slug>`; vent fan, `target_vent_fan_temp`).
- **Medium offset** and **High offset** — `number.*`, range **1–20 °F** (`OFFSET_MIN`/`OFFSET_MAX`). They define the fan-speed thresholds (CC-7). A room's fans **share a single** Medium/High offset pair (`fan_medium_offset` / `fan_high_offset`), not one pair per fan. The vent fan has no speed tiers, so it has no offset entities (CC-36/CC-37).
- **Use** toggle — `switch.*` (`use_ac` / `use_heater`; per fan, `use_fan__<slug>`; vent fan, `use_vent_fan`).
- **Manual mode** — one `switch.*` per room (`manual_mode`).
- **Fan-only override** — `switch.*` per applicable device (see CC-12).
- **Fan reverse** — `switch.*` per fan (`fan_reverse__<slug>`; see CC-22). The vent fan has no reverse control (on/off only).
- **Humidity target** — `number.*` **per humidity-triggered device**, range **30–90 %**, default **60**: per standalone fan (`fan_humidity_target__<slug>`), and for the vent fan (`vent_fan_humidity_target`). Created **only** when the room has a humidity sensor (and, for a fan's own target, that fan is configured) — CC-28/CC-37.
- **Humidity medium offset** and **Humidity high offset** — `number.*` **per standalone fan**, range **1–30 %** (`fan_humidity_medium_offset__<slug>` / `fan_humidity_high_offset__<slug>`, defaults 5/10). Same creation condition as that fan's humidity target (CC-28); they define that fan's humidity speed thresholds. The vent fan has no speed tiers, so it has no humidity offset entities (CC-37).

## Temperature comparison

- **CC-5** Setpoints and **fan-speed thresholds** (CC-7) **truncate to whole degrees** (`int(value)`) — targets and offsets alike. Displays may show tenths. The on/off **start/stop** decision is the exception: it uses the CC-27 hysteresis in tenths, not a truncated compare.
- **CC-27** The on/off decision uses an **asymmetric hysteresis deadband** to stop devices chattering near the target. A device's reported running mode (`cool`/`heat` for climates, on/off for the standalone fan) is the hysteresis state, so the engine stays stateless:
  - **Cooling / standalone fan** (target `T`): once running, keep conditioning while `room > T + 0.2`; once stopped, do not restart until `room >= T + 1.0` (the next whole degree past the target).
  - **Heating** (target `T`): mirror — once running, keep heating while `room < T − 0.2`; once stopped, do not restart until `room <= T − 1.0`.

  The `0.2 °F` off-margin (`HYSTERESIS_OFF` in `engine.py`) lets a device cool/heat to within a fifth of a degree of the target before stopping, while the restart threshold sits a full degree away — so the room must drift a whole degree past the target before the device cycles back on. A device in `fan_only` is **not** "running" for this purpose (its mode is neither `cool` nor `heat`), so it requires the full restart threshold to begin conditioning. The deadband is a fixed constant (not configurable).

## Idempotent command emission

- **CC-19** The engine emits a device command **only when it changes the device's state**. Every command is gated against the device's currently-reported state and skipped when already satisfied: HVAC-mode / fan-mode sets against the climate's reported mode/fan_mode, and turn-on/turn-off of climates, fans, and power switches against their current on/off state. Combined with CC-5, a fractional sensor change that leaves the truncated comparison unchanged produces **no commands** — important because many devices (e.g. heat pumps) audibly chirp on every received command. Whenever a setpoint would be sent at all (decision Cool or Heat — see CC-32; there is no setpoint gate to speak of in Fan Only/Off), the gate is **last-commanded-setpoint memory, not a direct compare against the device's reported echo**: the controller remembers the whole-°F value it last successfully commanded to each climate entity and passes it to the engine as input each evaluation (the engine itself stays stateless). This memory is per entity **per commanded HVAC mode**: the controller pairs the remembered value with the HVAC mode it was commanded under, and a decision-mode switch (Heat↔Cool) never matches memory — even when the two modes' desired values happen to coincide (e.g. both default to 70 °F) — so a mode switch always re-sends the setpoint once (devices may keep independent per-mode setpoints); memory then re-converges under the new mode. With **matching-mode** memory present, `SetTemperature` is sent **iff the desired whole-°F value differs from the last-commanded value** — the reported echo is not consulted at all. Memory, once present, is trusted **absolutely**: no bounded "drift" re-send against the echo is attempted, because any fixed threshold has a failure mode where a coarse-enough device grid (e.g. an internal clamp) crosses it on every evaluation and recreates the same beep loop the dedup exists to prevent. A genuine device-side setpoint change (user remote, device revert) is therefore deliberately **not fought** — it persists until the desired value itself changes, manual mode toggles, or HA restarts; manual mode (which clears the controller's memory while active, since the user may change device setpoints by hand) is the sanctioned way to override RCC's setpoint. The controller additionally drops a send whose **live-resolved** value (CC-35 send-time live clamp) equals the last-commanded value even when the engine's raw desired value didn't match memory — this covers a mode-transition evaluation (e.g. Fan Only → Cool/Heat) where the engine's desired setpoint was computed against a stale, mode-dependent (sometimes degenerate) snapshot of the device's range. **Without matching-mode memory** (fresh start, a decision-mode switch, or after manual mode cleared it): a device that has never reported a setpoint is sent once, unconditionally, and memory takes over from the next evaluation on; a reporting device is treated as converged once its echo is **less than** `SETPOINT_TOLERANCE` (1 °F) from desired (absorbing a °C-native device's own whole-°C grid rounding) — an echo exactly `SETPOINT_TOLERANCE` off is sent.
- **CC-32** `SetTemperature` is emitted **only while actively conditioning** — decision Cool or Heat — never in Fan Only or Off. The setpoint has no effect in fan-only, and devices report mode-dependent (sometimes degenerate) temperature ranges in that mode, so sending a setpoint there is at best a no-op and at worst nonsensical.

## Thresholds & fan-speed tiers

Speed is a 3-tier function of how far the room is past the target:

- **CC-6** Fan-speed tiers are **Low / Medium / High**, mapped to `10% / 50% / 100%` for percentage-controlled fans. For devices with named `fan_modes`, the tier label is matched onto the device's modes (preference list + substring fallback in [`fan_logic.py`](../../custom_components/btoddb_room_climate_controller/fan_logic.py)). A device with a discrete speed grid (`percentage_step`) snaps the commanded percentage up to its nearest step; the engine compares by grid index rather than raw percentage so it doesn't re-issue the same command every evaluation.
- **CC-7** Thresholds derive from the target plus the offsets:
  - **Cooling / fan** (hotter ⇒ faster): `medium = target + medium_offset`, `high = target + high_offset`, with `target < medium < high`. Room `≥ high` ⇒ High; `≥ medium` ⇒ Medium; else Low.
  - **Heating** (colder ⇒ faster): `medium = target − medium_offset`, `high = target − high_offset`, with `high < medium < target`. Room `≤ high` ⇒ High; `≤ medium` ⇒ Medium; else Low.
- **CC-8** The **high offset must exceed the medium offset** (constraint, clamped — see CC-16).

## Cooling (A/C) control — split rooms

- **CC-9** Decision: if **Use A/C** on **and** the room is past the cooling threshold (CC-27 hysteresis against target_cooling) → **Cool**. Else if fan-only override applies (CC-12) → **Fan Only**. Else → **Off**.
- The climate's own setpoint follows the room target by an offset (CC-35): `target_cooling − ac_setpoint_offset`. The controller **clamps** the raw value into the device's **live** range at send time (read after the HVAC-mode switch, since the range can be **mode-dependent** — e.g. an A/C advertises a default range while off and its real range only in `cool`), **1 °F inward**, to survive °F/°C display rounding: a whole-°C limit is reported as a whole °F up to 0.5° off, so a raw bound can convert back out of range (18 °C reports as 64 °F, but 64 °F = 17.78 °C is rejected while 65 °F = 18.33 °C is accepted). When clamping engages, the setpoint sits 1 °F inside the advertised limit.
- Fan speed while cooling follows the cooling tiers (CC-7) via climate `fan_mode` or the companion `ac_fan`.

## Heating control — split rooms

- **CC-10** Decision: if **Use heater** on **and** the room is past the heating threshold (CC-27 hysteresis against target_heating) → **Heat** (setpoint per CC-35: room target plus the heater offset). Else if the heater is fan-capable and (Use heater on **or** fan-only override applies) → **Fan Only**. Else → **Off**.
- **CC-33** *(superseded by CC-35)* — formerly: while decision is Heat, the climate's own setpoint was driven to the device's highest settable value (or an 85 °F fallback), removing the device's own thermostat from the loop entirely; a heating-target change alone triggered no `SetTemperature`. CC-35 inverts that: the setpoint now tracks the room target, so a target change while heating **does** send.
- **CC-34** *(superseded by CC-35)* — formerly: an optional per-room heater max-setpoint override capped a `max_temp` a device advertised wider than it actually accepted. Obsolete under CC-35, since setpoints now sit near the comfort target rather than at the device's extreme; a leftover stored override value is ignored (no migration).
- **CC-35** The device's own setpoint tracks the **room target** by a per-room offset, rather than being driven to the device's extreme (CC-33/CC-34's superseded design): while **Heat**, `device setpoint = int(target_heating + heater_setpoint_offset)`; while **Cool**, `device setpoint = int(target_cooling − ac_setpoint_offset)` — both truncated per CC-5 (replacing the old asymmetric `round(min)`/`int(max)`, since the value is now a comfort target, not a bound). `heater_setpoint_offset` and `ac_setpoint_offset` are optional per-room settings, range **0–20 °F**, default **2**; a combined room's Heat decision uses the heater offset even though it commands the shared A/C entity (the offset follows the decision, not the entity — see CC-11). The purpose is unchanged from CC-33/CC-9's original rationale: overshooting the room target by a couple of degrees keeps the device's own internal thermostat from stopping heating/cooling early, right around the comfort range instead of at the device's extreme. The engine's CC-19 gate clamps the *build-time snapshot*-derived desired value for its memory compare; the raw (unclamped) int is what `SetTemperature` actually carries; the controller then clamps *that* into the device's **live** range at send time, 1 °F inward (CC-9) — so the setpoint never leaves the device's advertised range. Because the setpoint now tracks the room target, a target change while conditioning emits exactly one new `SetTemperature` (inverting CC-33's old no-send property); CC-19 memory dedups any further evaluations. At default targets/offsets the Heat and Cool desired values can coincide (68 + 2 = 72 − 2 = 70); CC-19's mode-aware memory guarantees the setpoint is still sent once on a Heat↔Cool decision switch despite the numeric collision. Residual risk: a device advertising a wider range than it actually accepts (e.g. a device that rejects setpoints above 86 °F while its integration advertises 95 °F) can still fail-retry if the user sets a target within one offset of the device's true limit; at ordinary comfort targets (e.g. 68 °F heating target + 2 °F offset = 70 °F) this cannot occur.
- Fan speed while heating/fan-only follows the heating tiers (CC-7).

## Combined heat-pump control

- **CC-11** A combined climate picks one decision for the single entity: **Cool** (Use A/C on & room past the cooling threshold), **Heat** (Use heater on & room past the heating threshold), **Fan Only** (when an override/native-fan condition holds), else **Off**. Both thresholds use the CC-27 hysteresis, keyed on the single entity's reported mode (`cool`/`heat`). Setpoint follows CC-35 — the heater offset when heating, the A/C offset when cooling (the offset follows the *decision*, not the entity); no setpoint is sent in fan-only (CC-32). For a combined device, **fan-only override is offered for cooling only** (CC-12).

## Fan-only override

- **CC-12** Available only on **fan-capable cooling/heating** devices (and cooling-only on combined). When a device would otherwise turn **off**, the override can instead run it in **fan-only** mode:
  - **Use on, but not actively heating/cooling** → fan-only.
  - **Use off** → fan-only **only if** the room has no standalone fan, *or* its standalone fan's Use toggle is on; otherwise off.
  - Heaters additionally run fan-only **natively** whenever Use heater is on and they're not actively heating (no override needed).
  - **Multi-fan generalization:** "the room has a standalone fan" means whether **any** fan is configured, and "its standalone fan's Use toggle is on" means whether **any** fan's Use is on (the aggregate `use_fan = any(fan.use)`) — this preserves the single-fan behavior.

## Standalone fan control

A room may have a **list** of standalone fans (`fan_entities`, replacing the old
single `fan_entity`). Each fan has its **own** target temp, its **own** Use toggle,
and its **own** Fan reverse switch, but all of a room's fans **share** the room's
fan Medium/High **offsets** and the fan min/max **limits**. Fans are
**independent** — one may run while another is off. A standalone fan has **two**
triggers: the room's **temperature** and — when the room has a humidity sensor —
**that fan's own humidity target and offsets** (CC-28..CC-31) — each fan's
humidity settings are independent of every other fan's.

Cooling and heating remain **single-device** this round (out of scope). For
backward compatibility, a pre-existing single-fan room (config key `fan_entity`
and un-slugged `target_fan_temp` / `use_fan` / `fan_reverse` entities) is
**migrated** to the list form — its legacy entities are renamed to the slugged
per-fan keys.

- **CC-13** Each fan runs when **its own Use fan** toggle is on **and** the room is past that fan's temperature threshold (CC-27 cooling-style hysteresis against that fan's `target_fan`) **or past that fan's own humidity threshold (CC-28/CC-29)**; otherwise that fan is turned off. Every fan is evaluated independently against its own target, its own humidity target, and its own Use.
- **CC-14** While on, a fan's speed follows the cooling-style tiers (CC-7) against **its own** `target_fan` plus the room's **shared** fan offsets, mapped to 10/50/100% or the fan's preset modes. Because the offsets are shared, each fan's Medium/High thresholds are its own target plus the common offsets. When that fan's own humidity trigger is active the commanded speed is the **faster** of this tier and that fan's humidity tier (CC-29).

## Humidity trigger (standalone fans + vent fan)

A room with a humidity sensor can also run its standalone fans — and its vent fan
(CC-36) — to move damp air. Humidity is a **fan-only** concern — it never drives
cooling or heating.

- **CC-28** A room with a **humidity sensor** gets, **per standalone fan**, its own **Humidity target** `number` (unit %, range 30–90, default 60) and its own **Humidity medium/high offset** pair (unit %, range 1–30, defaults 5/10); the vent fan (when configured) gets only its own **Humidity target** `number` (same range/default), with no offsets — see CC-37. Humidity thresholds derive as in CC-7 (cooling-style): `medium = target + medium_offset`, `high = target + high_offset`, and the tier comparisons **truncate to whole %** per the CC-5 convention. Humidity affects **standalone fans and the vent fan only** — cooling/heating decisions (CC-9..CC-11) and companion fans remain temperature-only. Rooms without a humidity sensor get no humidity entities and behave exactly as before. **Note:** the former shared room-level humidity target/offset entities (one set per room, pre-#77) are **removed** from the entity registry on upgrade (clean cutover, no value migration) — every fan's and the vent fan's new per-device entities start at the defaults above, so existing humidity customizations reset.
- **CC-29** Temperature and humidity are **independent triggers** for each standalone fan, combined so they cooperate: a fan (with its Use on) runs when **either** the temperature trigger (CC-13/CC-27) **or** that fan's own humidity trigger (CC-30) wants it on, and turns off **only when both decline**. While running, speed is the **faster** of the temperature tier (CC-14) and that fan's own humidity tier (room humidity against that fan's CC-28 thresholds). The vent fan participates **on/off only** — it has no speed ladder (CC-36).
- **CC-30** The humidity on/off decision uses an **asymmetric hysteresis deadband** analogous to CC-27, keyed on the device's reported on/off state (humidity target `H`): once running, keep running while `humidity > H + 0.5`; once stopped, do not restart until `humidity >= H + 2.0`. The wider band (vs. temperature's 0.2/1.0) absorbs %RH sensor noise; the constants are fixed, not configurable. Each device (fan or vent fan) is evaluated against **its own** humidity target — a two-fan room's fans behave independently of one another.

  Because both triggers share a device's single on/off state, a device started by one trigger holds the *other* trigger in its keep-running band too — it stops only when temperature is within 0.2 °F of its target (or below) **and** humidity is within 0.5 % of its own target (or below). This can only lengthen a run, never cause cycling: any restart still requires a full CC-27/CC-30 restart-threshold crossing.
- **CC-31** Fail-safe: a missing or unreadable humidity reading disables the humidity trigger for every device (temperature-only behavior, CC-13/CC-36) — it never suppresses or forces conditioning. An unreadable **temperature** still skips the room's evaluation entirely (existing behavior), so humidity alone never drives control; a working temperature sensor is a prerequisite (documented limitation).

## Vent fan control

A room may have **exactly one** vent fan: a single on/off **`switch` or `fan`**
entity (no speed tiers, no direction) — e.g. a bathroom exhaust fan behind a smart
switch. It is a distinct device type (`vent_fan`) from the standalone fans above.

- **CC-36** With its own **Use** toggle on, the vent fan runs when the room is past its own **temperature** target (CC-27 cooling-style hysteresis against the vent fan's own reported on/off state) **or** past its own **humidity** target (CC-30 hysteresis); it turns off only when both decline. Commands are idempotent (CC-19): a command is emitted only on an actual on/off state change. An open window **never** suppresses it — CC-20's suppression covers active Cool/Heat only, not the vent fan. A missing or unreadable humidity reading degrades it to temperature-only (CC-31's fail-safe). An unavailable vent-fan entity is skipped (no commands, no crash).
- **CC-37** The vent fan's live entities: a `use_vent_fan` **Use** switch (default off), a `target_vent_fan_temp` **number** (°F, the room's per-device vent-fan min/max limits, default at the minimum), and — only when the room has a humidity sensor — a `vent_fan_humidity_target` **number** (%, range 30–90, default 60). It has **no** medium/high offset entities of its own (on/off only, no speed ladder). On/off commands are sent via the entity's **own domain** — `fan.turn_on`/`fan.turn_off` for a `fan.*` entity, `switch.turn_on`/`switch.turn_off` for a `switch.*` entity.

## Standalone fan direction

A reversible ceiling fan can spin **forward** or **reverse**. Reversibility is
**auto-detected** live, **per fan**, from each fan entity — there is no
config-flow toggle. Detection checks two signals: the standard HA DIRECTION
capability bit (`supported_features & FanEntityFeature.DIRECTION`), **or** the
presence of a `"reverse"` entry in the entity's `preset_modes` list (used by
integrations such as Dreo that express direction as a preset rather than a native
direction feature). **Each** standalone fan gets its **own** **Fan reverse**
`switch.*` entity (`fan_reverse__<slug>`); it is created unconditionally
(detection at platform setup would race fan integrations that load later) and is
simply inert for that fan when it is non-reversible. Direction control applies to
the **standalone fans only** — the A/C/heater companion fans are excluded by
design.

- **CC-22** When a standalone fan is **reversible and running**, and its reported direction differs from the requested one (**that fan's** Fan reverse switch on → `reverse`, off → `forward`), the engine emits a set-direction command for that fan. For fans with native DIRECTION support the controller calls `fan.set_direction`; for fans that use a `"reverse"` preset the controller calls `fan.set_preset_mode("reverse")` to engage and `fan.set_preset_mode(<forward-preset>)` to disengage (the forward preset is the first non-`"reverse"` entry in the entity's `preset_modes`).
- **CC-23** Idempotence (CC-19 extension): a fan's direction command is **suppressed when the reported direction already matches** the request. An unknown (`None`) reported direction never matches, so it emits.
- **CC-24** A **non-reversible** fan never receives a direction command, even when its Fan reverse switch is on.
- **CC-25** Direction is applied **only while that fan is actively running**: when a fan must start and reverse in the same evaluation, turn-on precedes set-direction; a reverse request while the fan is off emits nothing and takes effect at that fan's next turn-on.

## Manual mode

- **CC-15** When a room's **manual mode** switch is on, the engine does not drive that room — the user's manual device settings stand. Turning it off resumes control, which may overwrite settings per the rules. Scheduled profile applies are skipped in manual mode; an explicit "apply now" overrides this (see `profiles.md`).

## Window sensors

A room may configure zero or more optional **window** `binary_sensor`s
(`window_sensors`). They only affect their own room's conditioning.

- **CC-20** A room counts as **window open** when **any** of its window sensors reads `on`. While open, the engine suppresses the **Cool** and **Heat** decisions — a device actively cooling/heating is turned off via the normal OFF path. **Fan-only is not suppressed**: the standalone fan (CC-13), the vent fan (CC-36), a fan-capable heater's native fan-only, and the fan-only overrides (CC-12) still run, because they circulate air without spending heating/cooling energy. Profile applies still write uses/targets (see `profiles.md`); suppression is enforced at evaluation, so closing every window re-evaluates each device against the current targets with no re-apply. Manual mode (CC-15) still gates first — an open window never overrides a manually driven device.
- **CC-21** Fail-safe: a window sensor reading `unavailable`/`unknown` is treated as **closed**, per sensor. A room with all sensors closed (or none configured) behaves exactly as a room with no window sensor — conditioning is never suppressed on bad/missing data.

## Constraints (advisory clamping)

[`constraints.py`](../../custom_components/btoddb_room_climate_controller/constraints.py)
watches a room's target/offset numbers; on an invalid combination it **clamps**
the offending value and raises a **persistent notification** (HA notifications
tab) — non-blocking, no deprecated notify methods.

- **CC-16** High offset > medium offset (per device, and likewise **per standalone fan** for that fan's own **humidity** medium/high offsets — CC-28). The vent fan has no offsets, so this rule doesn't apply to it.
- **CC-17** **Heating target must stay below cooling target** (when the room has both).
- **CC-18** A device's `target ± high_offset` must stay within that device's configured min/max limits; the high offset is clamped to fit. The same rule applies to humidity, **per standalone fan**: that fan's `humidity_target + humidity_high_offset` must stay **≤ 100 %**, and that fan's humidity high offset is clamped to fit. The vent fan is **exempt** — its humidity target tops out at 90% (CC-37) and it has no offset to clamp.
- The validator ignores the echo of its own clamp writes so two rules can't ping-pong a value.

## Command timing

- **CC-19** Commands are spaced by `command_delay` (default 2 s) and a longer `power_on_delay` (default 3 s) after powering a switch, to let devices settle. The engine emits explicit `Delay` commands; the controller honors them.
- **CC-26** Command execution is **per-command resilient**: a single failed service call (e.g. a device rejecting an out-of-range setpoint) is logged with its `domain.service`/data and the controller **continues with the remaining commands** for the room rather than abandoning the evaluation.
