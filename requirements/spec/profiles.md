# Spec: Daily climate profiles

A **profile** is a named, scheduled preset for one room. At its scheduled time
(or on explicit "apply now") it copies its presets onto the room's live entities,
and the room's controller takes over from there (see `climate-control.md`).
Profiles are integration **storage records** — not HA automations.

## Data model

A profile ([`models.py`](../../custom_components/btoddb_room_climate_controller/models.py)
`Profile`) belongs to a room and holds:

- **PR-1** `id` (canonical 2-digit, e.g. `08`), `name` (display), `room` (room key), `enabled`, `time` (`HH:MM`, 24h), `fan_override` (bool), a per-device `presets` map for cooling/heating, and a **per-fan preset map** for the room's standalone fans (keyed by fan slug). The single profile-level `fan_reverse` is replaced by a per-fan `reverse` inside each fan preset.
- **PR-2** Each cooling/heating device preset (`DevicePreset`) has a **use** toggle and a **target temp**. Each **fan** preset carries a full per-fan set: **use** toggle, **target temp**, and **reverse**. New profiles default all presets to *use off, temp = the device's min limit* (and fan reverse off).
- **PR-3** A profile only carries presets for the device types its room has — and one fan preset per standalone fan the room has. Moving a profile to another room re-seeds presets (including per-fan presets) for that room's devices.
- Profiles are persisted in `.storage` ([`store.py`](../../custom_components/btoddb_room_climate_controller/store.py)) and exposed as entities (`switch.*` enabled, `time.*`, `number.*` presets) so they're editable outside the card too.

> **Legacy migration:** a pre-existing profile with a single fan preset and a
> profile-level `fan_reverse` is migrated to the per-fan preset map — the legacy
> single fan preset (use/target) and `fan_reverse` seed **every** fan in the room.

## What applying a profile does

- **PR-4** Applying writes the room's live entities: each cooling/heating device's **Use** switch and **target temp** number, **each fan's** own **Use** switch and **target temp** number, plus the room's **fan-only override** switch (when the room supports it). It does **not** touch the hardware directly — the controller reacts to those entity changes.
- **PR-12** Each fan preset's `reverse` is applied the same way: the apply writes **that fan's** **Fan reverse** switch, and the controller reacts per CC-22 — scheduled and explicit applies alike. Each per-fan reverse toggle is exposed as a `switch.*` entity like the other presets.
- **PR-5** A **scheduled** apply is **skipped if the room is in manual mode** (CC-15). An explicit **"apply now"** (`force=True`) applies regardless of manual mode.

## Scheduling

[`scheduler.py`](../../custom_components/btoddb_room_climate_controller/scheduler.py)
registers a time trigger per enabled profile and re-registers when a profile's
time or enabled flag changes.

- **PR-6** When a profile's `time` is reached and it is enabled, it is applied (subject to PR-5). Disabled profiles never fire.
- **PR-7** Sunrise/Sunset automations are a *separate* concern and are out of scope here — leave them untouched.

## Copy / paste

- **PR-8** **Copy** puts a profile's settings (device presets + fan override + the full per-fan presets, each with its use/target/reverse) on the clipboard. **Name and time are never copied.**
- **PR-9** **Paste** replaces the target profile's settings from the clipboard, but keeps its name and time. When pasting across rooms with different devices:
  - A clipboard temp for a device the target doesn't have is **ignored**. Likewise, a clipboard **fan entry for a fan the target room doesn't have is ignored** (paste is matched per fan slug).
  - A target device — or a target fan — with no value on the clipboard keeps its **current** value.
- **PR-10** A **"copy room"** action seeds a new profile from the room's current live settings, seeding **each fan's** preset (use/target/reverse) from that fan's live entities.

## Semantic checks

- **PR-11** A room **cannot have two profiles at the same time** (`find_time_conflict`). Two profiles may share a **name**.

> Note: profiles are storage records + entities, not HA automations/helpers, so the
> old "assign to a category" requirement no longer applies and is intentionally dropped.
