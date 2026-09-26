# BToddB Room Climate Controller

A Home Assistant custom integration (HACS-installable) that manages per-room
climate control and scheduled daily climate **profiles** from the UI.

Everything runs **in-process**: rooms and profiles are configured through the UI,
their controls are **native entities**, and a reactive engine drives your real
`climate` / `fan` / `switch` devices from the room's setpoints.

## What it does

- **Per-room reactive control.** For each room you wire its devices (A/C, heater,
  standalone fan, optional companion fans and power switches) and a temperature
  sensor. The integration creates the room's live controls as entities
  (`number.*` targets/offsets, `switch.*` use-toggles / manual-mode /
  fan-only-override) and a `RoomController` reacts to them — choosing
  cool/heat/fan-only/off, fan speed tiers, and honoring a manual-mode kill switch.
  Supports combined heat-pumps and split A/C + heater setups. A room's **humidity**
  is a second fan trigger: give the room a humidity sensor and a fan and it also
  gets a humidity target (%) plus medium/high offsets, so the fans run — and pick
  their speed — when the room is too damp as well as too warm.
- **Daily profiles.** A profile is a named, scheduled preset for a room (per-device
  target temps + use toggles + fan-only override). At its scheduled time (or via
  "apply now") it copies its presets onto the room's live entities and the
  controller takes it from there. Profiles are stored in `.storage` and edited
  through the card or their own entities (`switch.*`, `time.*`, `number.*`).
- **Abstraction sensors.** Per-room temperature/humidity/power mirrors plus a
  hub-level **Outdoor Temperature** mirror, so dashboards/graphs reference stable
  entities and the underlying source can be swapped in one place.
- **Areas.** Each room's devices/entities are placed in the room's HA area.

## Architecture

| Concern | Where |
|---|---|
| Hub + rooms | One config entry; each **room is a config subentry** (`config_flow.py`) |
| Profiles | Stored in `.storage` (`store.py`), managed over websocket (`websocket_api.py`) |
| Entities | Dynamic `number`/`switch`/`time`/`sensor` platforms, grouped by per-room and per-profile devices |
| Reactive engine | **Pure**, HA-free `engine.py` + `fan_logic.py` (unit-tested); executed by `controller.py` |
| Constraints | `constraints.py` clamps invalid target/offset combinations |
| Scheduling | `scheduler.py` (`async_track_time_change`), applying via `apply.py` |

The decision engine is deliberately a pure function — `compute_commands(inputs)`
returns an ordered list of device commands — so the bulk of the logic is testable
without Home Assistant (see `tests/`).

## Installation

### Via HACS (recommended)

Add this repository as a HACS **custom repository** (category: Integration),
install, then restart Home Assistant.

### Manual

1. Copy `custom_components/btoddb_room_climate_controller/` into your HA config's
   `custom_components/` directory.
2. Restart Home Assistant.

### Configure

1. **Settings → Devices & Services → Add Integration → "BToddB Room Climate Controller"**
   (optionally pick the outdoor temperature source).
2. On the entry, choose **Add room** and wire one room's devices, sensors, limits,
   and timing. Repeat per room. Wiring a **humidity sensor** on a room that has a
   standalone fan also creates that room's humidity target/offset entities, so
   humidity can drive the fans.

No YAML, no `secrets.yaml` token, no scripts to copy.

## The Lovelace card

The companion card ships inside the integration and **auto-registers its own
Lovelace resource** — nothing to add by hand. Add a card of type
`custom:room-climate-control` and pick a room in the visual editor; the card
discovers that room's sensors, devices, and helpers from the integration. A ready
sample dashboard is in [`examples/dashboard.yaml`](examples/dashboard.yaml).

The Energy/History graph dialogs use **lovelace-plotly-graph-card** (install via
HACS) and the integration's own graph time-range selector (a `select.*` entity it
creates — no hand-made `input_select` helper needed).

## Services

- `btoddb_room_climate_controller.apply_profile` `{ profile_id }` — apply a profile's presets now.
- `btoddb_room_climate_controller.set_manual_mode` `{ room, enabled }` — toggle a room's manual mode.

## Development

Run the pure-logic tests (no HA required):

```bash
python3 custom_components/btoddb_room_climate_controller/tests/test_engine.py
# or, with pytest:
pytest custom_components/btoddb_room_climate_controller/tests/
```

Rebuild the card after editing its TypeScript source:

```bash
cd custom_components/btoddb_room_climate_controller/card && npm install && npm run build
# deploy.sh additionally bumps the version and copies the bundle into www/
```
