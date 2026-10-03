# Integration code — guidance

Python custom integration `btoddb_room_climate_controller`. Behavior is specified in
[../../requirements/](../../requirements/) (start at `requirements/README.md`);
this file is about *how the code is laid out and tested*.

## Module map

| File | Responsibility |
|---|---|
| `engine.py` | **Pure** decision engine — `compute_commands(inputs)` returns ordered device commands. No HA imports. |
| `fan_logic.py` | **Pure** 3-tier fan-speed selection + device `fan_mode` matching. |
| `controller.py` | HA glue: gathers live state into `EngineInputs`, calls the engine, executes commands, honors manual mode. |
| `scheduler.py` | Fires profiles at their scheduled time (`async_track_time_change`). |
| `apply.py` | Copies a profile's presets onto the room's live entities. |
| `constraints.py` | Advisory clamping of invalid target/offset combinations + persistent notifications. |
| `config_flow.py` | Hub config entry + one **room per config subentry**. |
| `models.py` | `Room` / `Profile` data models and pure ID/time helpers. No HA imports. |
| `store.py` | Profile persistence in `.storage`. |
| `websocket_api.py` | Card ↔ integration profile CRUD. |
| `const.py` | All config keys, entity-key maps, defaults, limits. |
| `number.py` / `switch.py` / `time.py` / `select.py` / `sensor.py` | Dynamic entity platforms (per-room and per-profile). `sensor.py` also holds the per-room vent fan override status sensor (CC-40), fed by the controller over a dispatcher signal. |

## Rules

- **Python 3.14+.** Format and lint with **ruff** (`scripts/lint`); make coding choices with ruff's rules in mind.
- **Keep `engine.py` and `fan_logic.py` fully standalone** — they must import **nothing** from Home Assistant *and nothing from this integration* (no `const.py`, no `models.py`; `const.py` itself imports `homeassistant`). That total isolation is why they define their own input dataclasses and why `tests/test_engine.py` can load them without HA. `models.py` is looser: it imports `const.py`, so it isn't HA-free, but it must avoid **direct** `homeassistant` imports.
- **Temperatures are °F.** Setpoints and fan-speed thresholds truncate to whole degrees via `int()` (CC-5) — never switch those to `round()` or raw float math. **Exception:** the on/off start/stop decision uses the CC-27 float hysteresis (`_wants_cool` / `_wants_heat` in `engine.py`) — tenths are intentional there.
- **Entity identity is deterministic.** Use `room_uid()` / `profile_uid()` (`models.py`) and the `KEY_*` maps (`const.py`) — don't hand-build unique_ids.
- **`except A, B:` is correct here — don't "fix" it to `except (A, B):`.** This project targets Python 3.14, where [PEP 758](https://peps.python.org/pep-0758/) allows unparenthesized exception tuples when there's no `as` binding, and `ruff format` canonicalizes to that form (it will strip parens you add). It reads like the removed Python 2 `except Exc, name:` idiom but is not — there's no `as`, so both types are caught.

## Cross-file contracts (change both sides together)

- **`config_flow.py` ⇄ `strings.json` ⇄ `translations/en.json`** — any new/renamed config or option key needs matching entries in both translation files.
- **`websocket_api.py` ⇄ `card/src/schema.ts` & `card/src/types.ts`** — the websocket message shapes are the card↔integration contract. Change a message shape on one side and you must change the other.

## Tests

The engine is tested without Home Assistant, under plain pytest:

```bash
pytest custom_components/btoddb_room_climate_controller/tests/
```

`test_engine.py` uses a `_load()` import shim to pull in `engine`/`fan_logic`
directly, bypassing the package `__init__` (which imports HA). Keep that shim; don't
add `homeassistant` imports to the test module.

Add a failing engine test before changing control logic; map cases to the `CC-*`
rules in `requirements/spec/climate-control.md`.
