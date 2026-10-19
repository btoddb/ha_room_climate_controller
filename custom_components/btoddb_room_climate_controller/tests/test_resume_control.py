"""
Tests for the explicit "Resume control" action (CC-45, CC-L13).

Same approach as ``test_setpoint_hold.py``: ``controller.py`` imports directly
(Home Assistant is installed in this devcontainer), ``_resolve`` is
monkeypatched to bypass the entity registry, and ``hass`` is a tiny stand-in.
No live event loop: ``async_call_later`` is replaced with a recorder, and the
publish hooks / ``async_request_run`` / ``_notify`` are patched per test.
"""

import logging
from unittest.mock import MagicMock

import pytest

from custom_components.btoddb_room_climate_controller import controller as ctrl_mod
from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.const import KEY_MANUAL_MODE
from custom_components.btoddb_room_climate_controller.controller import (
    RoomController,
)

AC = "climate.office_ac"
HEATER = "climate.office_heater"
VENT = "switch.office_vent"
FAN = "fan.office_tower"
MANUAL_MODE = f"switch.{KEY_MANUAL_MODE}"


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": True,
        "has_heater": True,
        "has_fan": False,
        "combined": False,
        "ac_climate": AC,
        "heater_climate": HEATER,
        "fan_entities": (),
        "ac_fan_entity": None,
        "heater_fan_entity": None,
        "ac_power_switch": None,
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
            "vent_fan": {"min": 60.0, "max": 86.0},
        },
        "command_delay": 1.0,
        "power_on_delay": 2.0,
        "heater_setpoint_offset": 2,
        "ac_setpoint_offset": 2,
        "has_vent_fan": True,
        "vent_fan_entity": VENT,
        "setpoint_hold_minutes": 60,
        "notify_on_hold_end": True,
    }
    defaults.update(overrides)
    return models.Room(**defaults)


class _StubState:
    def __init__(self, value):
        self.state = str(value)
        self.attributes = {}


class _StubStates:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        return None if value is None else _StubState(value)


class _StubServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, dict]] = []

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, data, kwargs))


class _StubHass:
    def __init__(self, values):
        self.states = _StubStates(values)
        self.services = _StubServices()


@pytest.fixture
def timers(monkeypatch):
    """Replace ``async_call_later`` with a recorder of ``(delay, cb, cancel)``."""
    calls: list[tuple[float, object, MagicMock]] = []

    def fake_call_later(_hass, delay, cb):
        cancel = MagicMock()
        calls.append((delay, cb, cancel))
        return cancel

    monkeypatch.setattr(ctrl_mod, "async_call_later", fake_call_later)
    return calls


@pytest.fixture
def hooks():
    """Record publishes, ``async_request_run`` and ``_notify``."""
    return {"holds": [], "vent": [], "fans": [], "runs": [], "notified": []}


def _make_controller(hooks, **room_overrides):
    hass = _StubHass({MANUAL_MODE: "off", VENT: "off"})
    entry = MagicMock()
    entry.entry_id = "entry1"
    controller = RoomController(hass, entry, _room(**room_overrides))
    controller._resolve = lambda key, domain: f"{domain}.{key}"

    def publish_fan(entity_id):
        state = "active" if entity_id in controller._fan_overrides else "none"
        hooks["fans"].append((entity_id, state))

    controller._publish_fan_override = publish_fan

    def publish_hold(entity_id):
        hold = controller._setpoint_holds.get(entity_id)
        hooks["holds"].append((entity_id, "active" if hold else "none"))

    def publish_vent():
        if controller._vent_override is None:
            state = "none"
        else:
            state = "on" if controller._vent_override else "off"
        hooks["vent"].append(state)

    controller._publish_setpoint_hold = publish_hold
    controller._publish_vent_override = publish_vent
    controller.async_request_run = lambda trigger="evaluation": hooks["runs"].append(
        trigger
    )
    controller._notify = lambda device, message: hooks["notified"].append(
        (device, message)
    )
    return controller


def _reset(hooks):
    for recorded in hooks.values():
        recorded.clear()


def test_resume_clears_holds_and_vent_override(timers, hooks, caplog):
    """CC-45: resume ends the hold and vent override, silently, and re-runs."""
    controller = _make_controller(hooks)
    controller._last_commanded_setpoints[HEATER] = ("heat", 70)
    controller._last_commanded_setpoints[AC] = ("cool", 72)
    controller._start_setpoint_hold(HEATER, 74.0)
    controller._start_vent_override(on=True)
    cancels = [t[2] for t in timers]
    assert len(cancels) == 2
    _reset(hooks)

    with caplog.at_level(logging.DEBUG, logger=ctrl_mod.__name__):
        assert controller.async_resume_control("button") is True

    for cancel in cancels:
        cancel.assert_called_once()
    assert controller._setpoint_holds == {}
    assert controller._unsub_hold_timers == {}
    assert controller._vent_override is None
    assert controller._vent_override_until is None
    assert controller._unsub_vent_timer is None
    # CC-19: only the formerly held entity's memory is forgotten.
    assert HEATER not in controller._last_commanded_setpoints
    assert controller._last_commanded_setpoints[AC] == ("cool", 72)
    assert hooks["holds"] == [(HEATER, "none")]
    assert hooks["vent"] == ["none"]
    assert hooks["runs"] == ["resume control"]
    assert hooks["notified"] == []
    messages = [r.message for r in caplog.records]
    assert (
        "[room=office] Resume control (button): cleared 1 setpoint hold(s), "
        "vent override yes, 0 fan override(s)"
    ) in messages
    info = [r for r in caplog.records if "Resume control (button)" in r.message]
    assert [r.levelno for r in info] == [logging.INFO]


def test_resume_with_nothing_active(timers, hooks, caplog):
    """CC-45/CC-L13: nothing to resume still re-runs, publishes nothing."""
    controller = _make_controller(hooks)
    controller._last_commanded_setpoints[AC] = ("cool", 72)

    with caplog.at_level(logging.DEBUG, logger=ctrl_mod.__name__):
        assert controller.async_resume_control("service") is False

    assert timers == []
    assert hooks["holds"] == []
    assert hooks["vent"] == []
    assert hooks["runs"] == ["resume control"]
    assert hooks["notified"] == []
    assert controller._last_commanded_setpoints[AC] == ("cool", 72)
    records = [r for r in caplog.records if "Resume control" in r.message]
    assert [(r.levelno, r.message) for r in records] == [
        (logging.DEBUG, "[room=office] Resume control (service): nothing to resume")
    ]


def test_resume_only_vent_override(timers, hooks, caplog):
    """CC-45/CC-L13: a vent-only resume reports zero holds."""
    controller = _make_controller(hooks)
    controller._start_vent_override(on=False)
    _reset(hooks)

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        assert controller.async_resume_control("button") is True

    timers[0][2].assert_called_once()
    assert hooks["vent"] == ["none"]
    assert hooks["holds"] == []
    assert hooks["notified"] == []
    assert (
        "[room=office] Resume control (button): cleared 0 setpoint hold(s), "
        "vent override yes, 0 fan override(s)"
    ) in [r.message for r in caplog.records]


def test_resume_clears_fan_overrides(timers, hooks, caplog):
    """CC-45/CC-48/CC-L13: resume ends every fan override, silently, and re-runs."""
    other = "fan.office_ceiling"
    controller = _make_controller(
        hooks,
        has_ac=False,
        has_heater=False,
        ac_climate=None,
        heater_climate=None,
        has_vent_fan=False,
        vent_fan_entity=None,
        has_fan=True,
        fan_entities=(FAN, other),
    )
    controller._start_fan_override(FAN)
    controller._start_fan_override(other)
    cancels = [t[2] for t in timers]
    assert len(cancels) == 2
    _reset(hooks)

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        assert controller.async_resume_control("service") is True

    for cancel in cancels:
        cancel.assert_called_once()
    assert controller._fan_overrides == {}
    assert controller._unsub_fan_timers == {}
    assert hooks["fans"] == [(FAN, "none"), (other, "none")]
    assert hooks["runs"] == ["resume control"]
    assert hooks["notified"] == []
    messages = [r.message for r in caplog.records]
    assert (
        "[room=office] Resume control (service): cleared 0 setpoint hold(s), "
        "vent override no, 2 fan override(s)"
    ) in messages
    assert f"[room=office] Fan override cleared: {FAN}: resume control (service)" in (
        messages
    )


def test_resume_leaves_manual_mode_alone(timers, hooks):
    """CC-45: resuming never touches the Manual Mode switch."""
    controller = _make_controller(hooks)
    controller._start_setpoint_hold(AC, 68.0)
    controller._start_setpoint_hold(HEATER, 74.0)

    assert controller.async_resume_control("button") is True

    assert controller.hass.services.calls == []
    assert controller.hass.states.get(MANUAL_MODE).state == "off"
    assert controller._setpoint_holds == {}
    assert hooks["notified"] == []
    assert len(timers) == 2
