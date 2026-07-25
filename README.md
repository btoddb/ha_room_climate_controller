# BToddB Room Climate Controller

A Home Assistant **custom integration** (HACS-installable) for per-room climate
control and scheduled daily climate **profiles**, with a companion Lovelace card
it ships and auto-registers.

- Per-room reactive control of A/C / heater / fan devices (cool / heat / fan-only /
  off, plus fan-speed tiers) driven by a pure, unit-tested decision engine.
- Daily **profiles**: named, scheduled per-room presets (use toggles, fan override,
  target temps) created and edited from the card.
- Native entities, HA areas, abstraction sensors (room temp/humidity/power + a
  hub-level Outdoor Temperature), and plotly energy/history graphs. A room's
  **humidity** can also drive its fans — set a humidity target (%) plus
  medium/high offsets and the fans run when the room is too damp.

See [custom_components/btoddb_room_climate_controller/README.md](custom_components/btoddb_room_climate_controller/README.md)
for full details, and [requirements/](requirements/) for the design requirements.

## Install

- **HACS:** add this repo as a custom repository (category: Integration), install,
  restart HA, then **Settings → Devices & Services → Add Integration → "Room
  Climate Controller"** and **Add room** per room.
- **Manual:** copy `custom_components/btoddb_room_climate_controller/` into your HA
  `config/custom_components/`, restart, then add the integration as above.

A ready sample dashboard is in
[`custom_components/btoddb_room_climate_controller/examples/dashboard.yaml`](custom_components/btoddb_room_climate_controller/examples/dashboard.yaml).

## Development

This repo uses the standard HA integration dev scaffold (devcontainer + `config/`):

- `scripts/develop` — run Home Assistant locally against `config/`.
- `scripts/lint` — run ruff.
- `python3 custom_components/btoddb_room_climate_controller/tests/test_engine.py` — engine
  unit tests (no HA required).
- `cd custom_components/btoddb_room_climate_controller/card && npm install && npm run build`
  — rebuild the Lovelace card (edit `card/src/*.ts`, not the generated `www/` bundle).

See [CLAUDE.md](CLAUDE.md) for an architecture overview and [CONTRIBUTING.md](CONTRIBUTING.md).

### Installing integrations manually for testing

* Download zip file of custom component
  * wget https://github.com/JeffSteinbok/hass-dreo/releases/download/v1.9.10/pydreo_community-1.9.10.tar.gz

### Attributions

The Room Climate Control logo was generated in part by using: <a href="https://www.flaticon.com/free-icons/thermostat" title="thermostat icons">Thermostat icons created by Freepik - Flaticon</a>