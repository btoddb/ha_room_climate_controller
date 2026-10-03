"""
Tests for ``RoomController._vent_fan_control`` (CC-36/CC-37).

Home Assistant is installed in this devcontainer (see ``test_humidity_triggers_
evaluation.py``'s docstring), so ``controller.py``/``models.py`` import directly
here. A live ``hass``/event loop is avoided: ``_resolve`` is monkeypatched to
bypass the entity registry (mirrors ``test_constraints_humidity.py``'s
approach), and ``hass.states`` is backed by a tiny stand-in.
"""

from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.const import (
    DEFAULT_HUMIDITY_TARGET,
    DEVICE_VENT,
    KEY_TARGET,
    KEY_USE,
    KEY_VENT_HUMIDITY_TARGET,
)
from custom_components.btoddb_room_climate_controller.controller import (
    RoomController,
    _service_for,
)
from custom_components.btoddb_room_climate_controller.engine import (
    SwitchTurnOff,
    SwitchTurnOn,
)


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": False,
        "has_heater": False,
        "has_fan": False,
        "combined": False,
        "ac_climate": None,
        "heater_climate": None,
        "fan_entities": (),
        "ac_fan_entity": None,
        "heater_fan_entity": None,
        "ac_power_switch": None,
        "heater_power_switch": None,
        "temperature_sensor": "sensor.office_temp",
        "humidity_sensor": "sensor.office_humidity",
        "power_sensor": None,
        "window_sensors": (),
        "ac_fan_only": False,
        "heater_fan_only": False,
        "limits": {
            "cooling": {"min": 60.0, "max": 90.0},
            "heating": {"min": 50.0, "max": 80.0},
            "fan": {"min": 60.0, "max": 90.0},
            "vent_fan": {"min": 60.0, "max": 86.0},
        },
        "command_delay": 1.0,
        "power_on_delay": 2.0,
        "heater_setpoint_offset": 2,
        "ac_setpoint_offset": 2,
        "has_vent_fan": True,
        "vent_fan_entity": "switch.office_vent",
    }
    defaults.update(overrides)
    return models.Room(**defaults)


class _StubState:
    def __init__(self, value):
        self.state = str(value)


class _StubStates:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        return None if value is None else _StubState(value)


class _StubHass:
    def __init__(self, values):
        self.states = _StubStates(values)


def _make_controller(room, values):
    hass = _StubHass(values)
    entry = type("Entry", (), {"entry_id": "entry1"})()
    controller = RoomController(hass, entry, room)
    # Deterministic entity ids keyed by "<domain>.<key>", bypassing the entity
    # registry entirely (mirrors the constraints-test approach).
    controller._resolve = lambda key, domain: f"{domain}.{key}"
    return controller


def test_vent_fan_control_none_when_state_missing():
    """A vent entity with no state at all is skipped (CC-31)."""
    room = _room()
    controller = _make_controller(room, {})

    assert controller._vent_fan_control(room) is None


def test_vent_fan_control_none_when_state_unavailable():
    """An ``unavailable``/``unknown`` vent state is skipped the same way."""
    room = _room()
    controller = _make_controller(
        room,
        {"switch.office_vent": "unavailable", f"switch.{KEY_USE[DEVICE_VENT]}": "on"},
    )

    assert controller._vent_fan_control(room) is None

    controller = _make_controller(
        room, {"switch.office_vent": "unknown", f"switch.{KEY_USE[DEVICE_VENT]}": "on"}
    )
    assert controller._vent_fan_control(room) is None


def test_vent_fan_control_domain_from_switch_prefix():
    """A ``switch.`` vent entity id yields domain ``"switch"``."""
    room = _room(vent_fan_entity="switch.office_vent")
    values = {
        "switch.office_vent": "on",
        f"switch.{KEY_USE[DEVICE_VENT]}": "on",
        f"number.{KEY_TARGET[DEVICE_VENT]}": "72",
        f"number.{KEY_VENT_HUMIDITY_TARGET}": "55",
    }
    controller = _make_controller(room, values)

    vent = controller._vent_fan_control(room)

    assert vent is not None
    assert vent.entity_id == "switch.office_vent"
    assert vent.domain == "switch"
    assert vent.is_on is True
    assert vent.use is True
    assert vent.target == 72.0
    assert vent.humidity_target == 55.0


def test_vent_fan_control_domain_from_fan_prefix():
    """A ``fan.`` vent entity id yields domain ``"fan"``."""
    room = _room(vent_fan_entity="fan.office_vent")
    values = {
        "fan.office_vent": "off",
        f"switch.{KEY_USE[DEVICE_VENT]}": "off",
        f"number.{KEY_TARGET[DEVICE_VENT]}": "70",
    }
    controller = _make_controller(room, values)

    vent = controller._vent_fan_control(room)

    assert vent is not None
    assert vent.entity_id == "fan.office_vent"
    assert vent.domain == "fan"
    assert vent.is_on is False


def test_vent_fan_control_humidity_target_default_when_no_number_entity():
    """No live humidity-target number yet: falls back to DEFAULT_HUMIDITY_TARGET."""
    room = _room()
    values = {
        "switch.office_vent": "on",
        f"switch.{KEY_USE[DEVICE_VENT]}": "on",
        f"number.{KEY_TARGET[DEVICE_VENT]}": "72",
    }
    controller = _make_controller(room, values)

    vent = controller._vent_fan_control(room)

    assert vent is not None
    assert vent.humidity_target == float(DEFAULT_HUMIDITY_TARGET)


def test_vent_fan_control_humidity_target_none_without_humidity_sensor():
    """A room with no humidity sensor never gets a vent humidity target (CC-31)."""
    room = _room(humidity_sensor=None)
    values = {
        "switch.office_vent": "on",
        f"switch.{KEY_USE[DEVICE_VENT]}": "on",
        f"number.{KEY_TARGET[DEVICE_VENT]}": "72",
        f"number.{KEY_VENT_HUMIDITY_TARGET}": "55",
    }
    controller = _make_controller(room, values)

    vent = controller._vent_fan_control(room)

    assert vent is not None
    assert vent.humidity_target is None


def test_vent_fan_control_override_defaults_to_none():
    """CC-38: with no manual override active the built control carries None."""
    room = _room()
    values = {
        "switch.office_vent": "on",
        f"switch.{KEY_USE[DEVICE_VENT]}": "on",
        f"number.{KEY_TARGET[DEVICE_VENT]}": "72",
    }
    controller = _make_controller(room, values)

    vent = controller._vent_fan_control(room)

    assert vent is not None
    assert vent.override is None


def test_service_for_switch_commands_dispatch_to_entitys_own_domain():
    """CC-37: SwitchTurnOn/Off dispatch to the command's own entity domain."""
    assert _service_for(SwitchTurnOn("switch.x")) == (
        "switch",
        "turn_on",
        {"entity_id": "switch.x"},
    )
    assert _service_for(SwitchTurnOff("switch.x")) == (
        "switch",
        "turn_off",
        {"entity_id": "switch.x"},
    )
    assert _service_for(SwitchTurnOn("light.x")) == (
        "light",
        "turn_on",
        {"entity_id": "light.x"},
    )
    assert _service_for(SwitchTurnOff("light.x")) == (
        "light",
        "turn_off",
        {"entity_id": "light.x"},
    )
