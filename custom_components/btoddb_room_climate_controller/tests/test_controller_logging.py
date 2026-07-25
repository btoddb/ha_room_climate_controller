"""
Tests for the CC-L7 command-description formatter in ``controller.py``.

``controller.py`` is HA-coupled (unlike ``engine.py``), but ``_describe_command``
and ``_device_label`` only touch plain dataclasses (``Command`` subclasses from
``engine.py``, ``Room`` from ``models.py``) — no live HA state — so they're
tested directly here with the same import shim ``test_engine.py`` uses.
"""

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
