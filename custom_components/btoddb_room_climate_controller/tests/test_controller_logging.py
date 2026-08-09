"""
Tests for ``controller.py``'s pure-ish helpers and CC-19 setpoint memory.

``controller.py`` is HA-coupled (unlike ``engine.py``), but most of what's
tested here only touches plain dataclasses (``Command`` subclasses from
``engine.py``, ``Room`` from ``models.py``) or the small surface of
``hass.states``/``hass.services`` that lightweight stubs below cover — no full
HA test harness needed, so it's tested directly with the same import shim
``test_engine.py`` uses.
"""

import asyncio
import importlib.util
import pathlib
import sys
import types

_PKG = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str):
    """Load a module under a throwaway package so its relative imports work."""
    if "rc_controller" not in sys.modules:
        pkg = types.ModuleType("rc_controller")
        pkg.__path__ = [str(_PKG)]
        sys.modules["rc_controller"] = pkg
    spec = importlib.util.spec_from_file_location(
        f"rc_controller.{name}", _PKG / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"rc_controller.{name}"] = module
    spec.loader.exec_module(module)
    return module


controller = _load("controller")
models = _load("models")

from rc_controller.controller import (  # noqa: E402
    _describe_command,
    _threshold_context,
)
from rc_controller.engine import (  # noqa: E402
    ClimateInfo,
    EngineInputs,
    FanControl,
    FanInfo,
    FanSetDirection,
    FanSetPercentage,
    FanSetPreset,
    FanTurnOff,
    FanTurnOn,
    SetFanMode,
    SetHvacMode,
    SetTemperature,
    SwitchTurnOff,
    SwitchTurnOn,
    TurnOffClimate,
)


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": True,
        "has_heater": False,
        "has_fan": True,
        "combined": False,
        "ac_climate": "climate.office_ac",
        "heater_climate": None,
        "fan_entities": ("fan.office",),
        "ac_fan_entity": "fan.office_ac_fan",
        "heater_fan_entity": None,
        "ac_power_switch": "switch.office_ac_power",
        "heater_power_switch": None,
        "temperature_sensor": "sensor.office_temp",
        "humidity_sensor": None,
        "power_sensor": None,
        "window_sensors": (),
        "ac_fan_only": False,
        "heater_fan_only": False,
        "limits": {
            "cooling": {"min": 60.0, "max": 90.0},
            "heating": {"min": 50.0, "max": 80.0},
            "fan": {"min": 60.0, "max": 90.0},
        },
        "command_delay": 1.0,
        "power_on_delay": 2.0,
    }
    defaults.update(overrides)
    return models.Room(**defaults)


def test_describe_command_maps_each_command_to_a_phrase():
    room = _room()
    cases = [
        (SetHvacMode("climate.office_ac", "cool"), "A/C → cool"),
        (TurnOffClimate("climate.office_ac"), "A/C off"),
        (SetTemperature("climate.office_ac", 65, "cool"), "A/C setpoint → 65°F"),
        (SetFanMode("climate.office_ac", "high"), "A/C fan speed → high"),
        (FanTurnOn("fan.office"), "Fan office on"),
        (FanTurnOff("fan.office"), "Fan office off"),
        (FanSetPreset("fan.office_ac_fan", "medium"), "A/C fan speed → medium"),
        (FanSetPercentage("fan.office", 60), "Fan office speed → 60%"),
        (FanSetDirection("fan.office", "reverse"), "Fan office direction → reverse"),
        (SwitchTurnOn("switch.office_ac_power"), "A/C power on"),
        (SwitchTurnOff("switch.office_ac_power"), "A/C power off"),
    ]
    for cmd, expected in cases:
        assert _describe_command(cmd, room) == expected


def test_describe_command_falls_back_to_entity_id_for_unknown_entity():
    room = _room()
    cmd = SetHvacMode("climate.some_other_device", "cool")
    assert _describe_command(cmd, room) == "climate.some_other_device → cool"


def _inputs(**overrides):
    defaults = {
        "combined": False,
        "room_temp": 78.0,
        "ac": None,
        "heater": None,
        "fans": (
            FanControl(
                info=FanInfo(
                    "fan.office",
                    is_on=False,
                    preset_mode=None,
                    percentage=0,
                    preset_modes=(),
                ),
                use=False,
                target=72.0,
                medium=75.0,
                high=78.0,
                reverse=False,
            ),
        ),
        "ac_fan": None,
        "heater_fan": None,
        "ac_power": None,
        "heater_power": None,
        "use_ac": True,
        "use_heater": False,
        "ac_fan_only_override": False,
        "heater_fan_only_override": False,
        "target_cooling": 72.0,
        "cooling_medium": 75.0,
        "cooling_high": 78.0,
        "target_heating": 68.0,
        "heating_medium": 65.0,
        "heating_high": 62.0,
        "command_delay_ms": 1000,
        "power_on_delay_ms": 2000,
    }
    defaults.update(overrides)
    return EngineInputs(**defaults)


def test_threshold_context_only_lists_devices_the_room_has():
    room = _room(has_ac=True, has_heater=False, has_fan=True)
    context = _threshold_context(room, _inputs())
    assert "temp 78°F" in context
    assert "cooling target 72°F" in context
    assert "fan office target 72°F" in context
    assert "heating target" not in context
    assert "humidity" not in context


def test_threshold_context_includes_humidity_when_present():
    """CC-L7: a room with a humidity trigger reports its %RH thresholds."""
    room = _room(has_ac=True, has_heater=False, has_fan=True)
    inputs = _inputs(
        room_humidity=55.0,
        humidity_target=60.0,
        humidity_medium=65.0,
        humidity_high=70.0,
    )
    context = _threshold_context(room, inputs)
    assert "humidity 55% target 60% (med 65% high 70%)" in context


# -- CC-19 last-commanded-setpoint memory (controller._climate_info) --------
class _StubState:
    """Minimal ``homeassistant.core.State`` stand-in: just ``state``/``attributes``."""

    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class _StubStates:
    """Minimal ``hass.states`` stand-in backed by a plain dict."""

    def __init__(self, entities):
        self._entities = entities

    def get(self, entity_id):
        return self._entities.get(entity_id)


class _StubServices:
    """Minimal ``hass.services`` stand-in: records calls, can simulate failure."""

    def __init__(self, *, raise_on=None):
        self.calls: list[tuple[str, str, dict]] = []
        self._raise_on = raise_on or set()

    async def async_call(self, domain, service, data, blocking=True):  # noqa: ARG002
        self.calls.append((domain, service, data))
        if (domain, service) in self._raise_on:
            msg = f"simulated failure: {domain}.{service}"
            raise RuntimeError(msg)


class _StubHass:
    """Minimal ``HomeAssistant`` stand-in exposing ``states``/``services``."""

    def __init__(self, entities, *, raise_on=None):
        self.states = _StubStates(entities)
        self.services = _StubServices(raise_on=raise_on)


def test_climate_info_threads_last_commanded_setpoint():
    """
    CC-19: ``_climate_info`` populates ``last_commanded_setpoint`` from memory.

    Uses lightweight stubs rather than a full HA harness — ``_climate_info``
    only reads ``hass.states.get(entity_id).attributes`` and the controller's
    own ``_last_commanded_setpoints`` dict, so no service-call plumbing is
    needed.
    """
    hass = _StubHass(
        {
            "climate.office_ac": _StubState(
                "cool",
                {
                    "hvac_mode": "cool",
                    "hvac_modes": ["off", "cool"],
                    "supported_features": 1,
                    "temperature": 65.3,
                },
            )
        }
    )
    room = _room()
    ctrl = controller.RoomController(hass, entry=None, room=room)
    ctrl._last_commanded_setpoints["climate.office_ac"] = 65

    info = ctrl._climate_info("climate.office_ac")

    assert info.last_commanded_setpoint == 65
    assert info.current_setpoint == 65.3


def test_climate_info_last_commanded_setpoint_defaults_to_none():
    """An entity never commanded (or cleared, e.g. by manual mode) reports None."""
    hass = _StubHass(
        {
            "climate.office_ac": _StubState(
                "cool",
                {
                    "hvac_mode": "cool",
                    "hvac_modes": ["off", "cool"],
                    "supported_features": 1,
                    "temperature": 65.3,
                },
            )
        }
    )
    room = _room()
    ctrl = controller.RoomController(hass, entry=None, room=room)

    info = ctrl._climate_info("climate.office_ac")

    assert info.last_commanded_setpoint is None


# -- CC-19 last-commanded-setpoint memory (controller._run) -----------------
def _ac_climate(**overrides):
    """``ClimateInfo`` defaults for the ``_run``-level CC-19 memory tests below."""
    defaults = dict(
        entity_id="climate.office_ac",
        hvac_mode="cool",
        fan_mode=None,
        hvac_modes=("off", "cool"),
        fan_modes=(),
        min_temp=61.0,
        max_temp=None,
        supports_set_temp=True,
        current_setpoint=None,
        last_commanded_setpoint=None,
    )
    defaults.update(overrides)
    return ClimateInfo(**defaults)


def test_run_records_resolved_setpoint_not_raw_command_value():
    """
    CC-19: the controller remembers the live-resolved value it actually sent.

    Not the engine's raw pre-clamp command value — CC-9's send-time clamp
    applies first. ``_run`` is a plain coroutine, driven directly with
    ``asyncio.run`` here rather than a full HA test harness;
    ``ctrl._build_inputs`` is monkeypatched to hand back a canned
    ``EngineInputs`` so the real engine (``compute_commands``) still drives
    what gets sent.

    The engine's raw ``ac_setpoint_int`` here is 61 (from the build-time
    ``min_temp=61.0`` snapshot), but the device's *live* reported range
    (``min_temp=62``) clamps it up to 63 at send time (CC-9) — that's the
    value that must land in memory, or the next evaluation's
    memory-vs-desired compare would be comparing against a value never
    actually sent to the device.
    """
    entity_id = "climate.office_ac"
    hass = _StubHass({entity_id: _StubState("cool", {"min_temp": 62, "max_temp": 86})})
    room = _room()
    ctrl = controller.RoomController(hass, entry=None, room=room)
    ctrl._build_inputs = lambda: _inputs(
        ac=_ac_climate(), fans=(), command_delay_ms=0, power_on_delay_ms=0
    )

    asyncio.run(ctrl._run("test"))

    assert hass.services.calls == [
        (
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": 63, "hvac_mode": "cool"},
        ),
    ]
    assert ctrl._last_commanded_setpoints[entity_id] == 63


def test_run_records_nothing_on_service_call_failure():
    """CC-19: a failed service call is not recorded, so it retries next evaluation."""
    entity_id = "climate.office_ac"
    hass = _StubHass(
        {entity_id: _StubState("cool", {"min_temp": 62, "max_temp": 86})},
        raise_on={("climate", "set_temperature")},
    )
    room = _room()
    ctrl = controller.RoomController(hass, entry=None, room=room)
    ctrl._build_inputs = lambda: _inputs(
        ac=_ac_climate(), fans=(), command_delay_ms=0, power_on_delay_ms=0
    )

    asyncio.run(ctrl._run("test"))

    assert len(hass.services.calls) == 1  # the call was attempted...
    assert ctrl._last_commanded_setpoints == {}  # ...but not recorded


def test_build_inputs_clears_memory_when_manual_mode_active():
    """
    CC-19: re-activation after manual mode must re-enforce, not trust stale memory.

    ``_switch_state`` is monkeypatched directly (bypassing the entity
    registry lookup ``_resolve`` needs) so this stays a lightweight-stub
    test rather than requiring a full HA harness.
    """
    hass = _StubHass({})
    room = _room()
    ctrl = controller.RoomController(hass, entry=None, room=room)
    ctrl._last_commanded_setpoints["climate.office_ac"] = 65
    ctrl._switch_state = lambda key, default=False: True  # noqa: ARG005 (manual mode)

    result = ctrl._build_inputs()

    assert result is None
    assert ctrl._last_commanded_setpoints == {}


def test_run_skips_set_temperature_when_resolved_value_matches_memory():
    """
    CC-19 (review cycle 2): drop a send whose live-resolved value matches memory.

    Models a Fan Only -> Cool transition on a heat pump: the engine's
    *build-time* ``ClimateInfo`` snapshot still reports a degenerate range
    (``min_temp=0``/``max_temp=2``, as this device does mid-transition), so
    the engine computes a garbage desired setpoint and decides to send. But
    the device's *live* range (read by the controller at resolve time) is
    the real cool-mode range, and resolving the engine's raw command against
    it lands back on 63 — exactly what memory already has from the last cool
    cycle. The controller drops that send, while a second command in the
    same batch (``SetFanMode``) still goes out untouched.
    """
    entity_id = "climate.office_ac"
    hass = _StubHass({entity_id: _StubState("cool", {"min_temp": 62, "max_temp": 86})})
    room = _room()
    ctrl = controller.RoomController(hass, entry=None, room=room)
    ctrl._last_commanded_setpoints[entity_id] = 63
    ctrl._build_inputs = lambda: _inputs(
        ac=_ac_climate(
            min_temp=0.0,
            max_temp=2.0,
            fan_modes=("low",),
            current_setpoint=70.0,
        ),
        fans=(),
        room_temp=70.0,
        target_cooling=65.0,
        cooling_medium=90.0,
        cooling_high=95.0,
        command_delay_ms=0,
        power_on_delay_ms=0,
    )

    asyncio.run(ctrl._run("test"))

    assert hass.services.calls == [
        ("climate", "set_fan_mode", {"entity_id": entity_id, "fan_mode": "low"}),
    ]
    assert ctrl._last_commanded_setpoints[entity_id] == 63
