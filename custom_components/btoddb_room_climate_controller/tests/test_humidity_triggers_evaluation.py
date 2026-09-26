"""
Tests for CC-L2: a humidity sensor change is logged and triggers an evaluation.

The evaluation is requested only in a room with fans; humidity is inert without
them (CC-28), so a fan-less room logs the change and stops there.

Home Assistant is installed in this devcontainer (unlike the fully HA-free
``engine``/``fan_logic`` modules), so ``controller.py`` and ``models.py`` import
directly here rather than through the ``_load`` shim ``test_engine.py`` uses for
the dependency-free modules. Only a *live* ``hass``/event loop is avoided — the
controller is driven with a plain ``unittest.mock.MagicMock`` in place of
``HomeAssistant``.
"""

import logging
from unittest.mock import MagicMock, patch

from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.const import LOGGER_SENSOR
from custom_components.btoddb_room_climate_controller.controller import RoomController


def _room(**overrides):
    """Build a room; it has a fan by default, since humidity is inert without one."""
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
        "fan_entities": ("fan.office_tower",),
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
        },
        "command_delay": 1.0,
        "power_on_delay": 2.0,
    }
    defaults.update(overrides)
    return models.Room(**defaults)


class _FakeState:
    """Minimal stand-in for ``homeassistant.core.State`` — only ``.state`` is read."""

    def __init__(self, state: str) -> None:
        self.state = state


class _FakeEvent:
    """Minimal stand-in for the ``Event`` that ``_on_change`` reads."""

    def __init__(self, data: dict) -> None:
        self.data = data


def _change_event(entity_id: str, old: str, new: str) -> _FakeEvent:
    return _FakeEvent(
        {
            "entity_id": entity_id,
            "old_state": _FakeState(old),
            "new_state": _FakeState(new),
        }
    )


def _make_controller(room) -> tuple[RoomController, MagicMock]:
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    return RoomController(hass, entry, room), hass


def _close_scheduled(hass: MagicMock) -> None:
    """Avoid a "never awaited" warning for the scheduled coroutine."""
    coro = hass.async_create_task.call_args.args[0]
    coro.close()


def test_humidity_change_is_logged_and_schedules_a_run(caplog):
    """CC-L2: a humidity change is logged and requests an evaluation."""
    room = _room()
    controller, hass = _make_controller(room)

    with caplog.at_level(logging.INFO, logger=LOGGER_SENSOR):
        controller._on_change(_change_event("sensor.office_humidity", "44.43", "44.44"))

    hass.async_create_task.assert_called_once()
    _close_scheduled(hass)

    messages = [r.message for r in caplog.records if r.name == LOGGER_SENSOR]
    assert any("Humidity changed: 44.43 → 44.44%" in m for m in messages)
    assert any("[room=office]" in m for m in messages)


def test_humidity_trigger_reason_names_the_reading():
    """CC-L2/CC-L7: the requested evaluation is tagged ``humidity <old>→<new>%``."""
    room = _room()
    controller, _hass = _make_controller(room)

    with patch.object(controller, "async_request_run") as request_run:
        controller._on_change(_change_event("sensor.office_humidity", "44.43", "52.0"))

    request_run.assert_called_once_with(trigger="humidity 44.43→52.0%")


def test_humidity_change_without_fans_is_logged_but_does_not_evaluate(caplog):
    """CC-28: humidity is inert in a fan-less room, so no evaluation is requested."""
    room = _room(has_fan=False, fan_entities=())
    controller, hass = _make_controller(room)

    with caplog.at_level(logging.INFO, logger=LOGGER_SENSOR):
        controller._on_change(_change_event("sensor.office_humidity", "50", "60"))

    hass.async_create_task.assert_not_called()

    messages = [r.message for r in caplog.records if r.name == LOGGER_SENSOR]
    assert any("Humidity changed: 50 → 60%" in m for m in messages)


def test_unchanged_humidity_without_fans_does_not_evaluate():
    """The fan-less early return covers unchanged readings too."""
    room = _room(has_fan=False, fan_entities=())
    controller, hass = _make_controller(room)

    controller._on_change(_change_event("sensor.office_humidity", "50", "50"))

    hass.async_create_task.assert_not_called()


def test_unchanged_humidity_still_schedules_a_run_without_logging(caplog):
    """An unchanged reading isn't logged, but still evaluates like any tracked id."""
    room = _room()
    controller, hass = _make_controller(room)

    with caplog.at_level(logging.INFO, logger=LOGGER_SENSOR):
        controller._on_change(_change_event("sensor.office_humidity", "50", "50"))

    hass.async_create_task.assert_called_once()
    _close_scheduled(hass)

    messages = [r.message for r in caplog.records if r.name == LOGGER_SENSOR]
    assert not any("Humidity changed" in m for m in messages)


def test_temperature_change_schedules_a_run(caplog):
    """A temperature change (like humidity) does request an evaluation."""
    room = _room()
    controller, hass = _make_controller(room)

    with caplog.at_level(logging.INFO, logger=LOGGER_SENSOR):
        controller._on_change(_change_event("sensor.office_temp", "72", "73"))

    hass.async_create_task.assert_called_once()
    _close_scheduled(hass)

    messages = [r.message for r in caplog.records if r.name == LOGGER_SENSOR]
    assert any("Temperature changed: 72 → 73°F" in m for m in messages)


def test_humidity_change_with_no_temperature_sensor_still_evaluates(caplog):
    """A room with only a humidity sensor configured still evaluates on change."""
    room = _room(temperature_sensor=None)
    controller, hass = _make_controller(room)

    with caplog.at_level(logging.INFO, logger=LOGGER_SENSOR):
        controller._on_change(_change_event("sensor.office_humidity", "50", "51"))

    hass.async_create_task.assert_called_once()
    _close_scheduled(hass)

    messages = [r.message for r in caplog.records if r.name == LOGGER_SENSOR]
    assert any("Humidity changed: 50 → 51%" in m for m in messages)
