# Spec: Daily climate profiles

A **profile** is a named, scheduled preset for one room. At its scheduled time
(or on explicit "apply now") it copies its presets onto the room's live entities,
and the room's controller takes over from there (see `climate-control.md`).
Profiles are integration **storage records** — not HA automations.

## Data model

A profile ([`models.py`](../../custom_components/btoddb_room_climate_controller/models.py)
`Profile`) belongs to a room and holds:

- **PR-1** `id` (canonical 2-digit, e.g. `08`), `name` (display), `room` (room key), `enabled`, `time` (`HH:MM`, 24h), `fan_override` (bool), a per-device `presets` map for cooling/heating/**vent fan**, and a **per-fan preset map** for the room's standalone fans (keyed by fan slug). The single profile-level `fan_reverse` is replaced by a per-fan `reverse` inside each fan preset.
- **PR-2** Each cooling/heating device preset (`DevicePreset`) has a **use** toggle and a **target temp**. The **vent fan**'s preset (also a `DevicePreset`) additionally carries a **humidity target** (PR-13) — `None`/absent for cooling and heating, which have no humidity concept. Each **fan** preset carries a full per-fan set: **use** toggle, **target temp**, **reverse**, and — when the room has a humidity sensor — that fan's own **humidity target** (PR-14; `None` otherwise). New profiles default all presets to *use off, temp = the device's min limit* (fan reverse off; humidity target 60 when the room has a humidity sensor, else absent).
- **PR-3** A profile only carries presets for the device types its room has — and one fan preset per standalone fan the room has. Moving a profile to another room re-seeds presets (including per-fan presets and the vent-fan preset) for that room's devices.
- Profiles are persisted in `.storage` ([`store.py`](../../custom_components/btoddb_room_climate_controller/store.py)) and exposed as entities (`switch.*` enabled, `time.*`, `number.*` presets) so they're editable outside the card too.

> **Legacy migration:** a pre-existing profile with a single fan preset and a
> profile-level `fan_reverse` is migrated to the per-fan preset map — the legacy
> single fan preset (use/target) and `fan_reverse` seed **every** fan in the room.

## What applying a profile does

- **PR-4** Applying writes the room's live entities: each cooling/heating/vent-fan device's **Use** switch and **target temp** number, **each fan's** own **Use** switch and **target temp** number, plus the room's **fan-only override** switch (when the room supports it). It does **not** touch the hardware directly — the controller reacts to those entity changes.
- **PR-12** Each fan preset's `reverse` is applied the same way: the apply writes **that fan's** **Fan reverse** switch, and the controller reacts per CC-22 — scheduled and explicit applies alike. Each per-fan reverse toggle is exposed as a `switch.*` entity like the other presets.
- **PR-13** The vent fan's preset additionally applies its **humidity target** — when the preset has one set and the room has a humidity sensor, apply writes the vent fan's `vent_fan_humidity_target` number. A profile stored before this entity existed has `humidity = None` on its vent preset, so that write is skipped (no error) and the live number keeps its current value.
- **PR-14** Each fan preset additionally applies **that fan's own humidity target** the same way — writes `fan_humidity_target__<slug>` when the preset has a value set and the room has a humidity sensor; absent/`None` skips the write. Humidity **offsets** (medium/high) are **live-only settings, not preset values** — applying a profile never touches a fan's or the vent fan's offset numbers, mirroring how temperature offsets were already excluded from presets. A profile stored before humidity presets existed (no `humidity` key at all) loads with `humidity = None` on every preset and applies exactly as it did before — the skip above makes the humidity write a no-op rather than an error.
- **PR-5** A **scheduled** apply is **skipped if the room is in manual mode** (CC-15). An explicit **"apply now"** (`force=True`) applies regardless of manual mode.

## Scheduling

[`scheduler.py`](../../custom_components/btoddb_room_climate_controller/scheduler.py)
registers a time trigger per enabled profile and re-registers when a profile's
time or enabled flag changes.

- **PR-6** When a profile's `time` is reached and it is enabled, it is applied (subject to PR-5). Disabled profiles never fire.
- **PR-7** Sunrise/Sunset automations are a *separate* concern and are out of scope here — leave them untouched.

## Copy / paste

- **PR-8** **Copy** puts a profile's settings (device presets, including the vent-fan preset's use/target/humidity + fan override, and the full per-fan presets, each with its use/target/reverse/humidity) on the clipboard. **Name and time are never copied.** A clipboard payload from before humidity presets existed (no `humidity` field) still parses and pastes; the missing field is treated as absent, not an error.
- **PR-9** **Paste** replaces the target profile's settings from the clipboard, but keeps its name and time. When pasting across rooms with different devices:
  - A clipboard temp (or humidity target) for a device the target doesn't have is **ignored**. Likewise, a clipboard **fan entry for a fan the target room doesn't have is ignored** (paste is matched per fan slug).
  - A target device — or a target fan — with no value on the clipboard keeps its **current** value. A target room with no humidity sensor ignores any humidity values on the clipboard.
- **PR-10** A **"copy room"** action seeds a new profile from the room's current live settings, seeding **each fan's** preset (use/target/reverse/humidity) and, when the room has a vent fan, the **vent preset** (use/target/humidity) from their live entities.

## Semantic checks

- **PR-11** A room **cannot have two profiles at the same time** (`find_time_conflict`). Two profiles may share a **name**.

> Note: profiles are storage records + entities, not HA automations/helpers, so the
> old "assign to a category" requirement no longer applies and is intentionally dropped.
