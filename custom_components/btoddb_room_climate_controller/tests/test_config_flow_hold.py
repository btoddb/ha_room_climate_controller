"""
Tests for the room devices-step setpoint hold fields (CC-41/CC-44).

``config_flow.py`` imports directly (Home Assistant is installed in this
devcontainer). ``_device_fields()`` only reads ``self._data``, so the handler is
instantiated bare and its accumulated data set per test.
"""

import asyncio

import pytest
import voluptuous as vol

from custom_components.btoddb_room_climate_controller.config_flow import (
    RoomSubentryFlowHandler,
)
from custom_components.btoddb_room_climate_controller.const import (
    CONF_COMBINED,
    CONF_HAS_AC,
    CONF_HAS_FAN,
    CONF_HAS_HEATER,
    CONF_HAS_VENT_FAN,
    CONF_NOTIFY_ON_HOLD_END,
    CONF_ROOM_KEY,
    CONF_SETPOINT_HOLD_MINUTES,
)


def _fields(**data) -> dict[str, vol.Marker]:
    handler = RoomSubentryFlowHandler()
    handler._data = {CONF_ROOM_KEY: "office", **data}
    return {marker.schema: marker for marker, _ in handler._device_fields()}


@pytest.mark.parametrize(
    "data",
    [
        {CONF_HAS_AC: True},
        {CONF_HAS_HEATER: True},
        {CONF_HAS_AC: True, CONF_HAS_HEATER: True},
        {CONF_HAS_AC: True, CONF_HAS_HEATER: True, CONF_COMBINED: True},
    ],
    ids=["ac_only", "heater_only", "split", "combined"],
)
def test_hold_fields_present_for_climate_rooms(data):
    fields = _fields(**data)
    assert CONF_SETPOINT_HOLD_MINUTES in fields
    assert CONF_NOTIFY_ON_HOLD_END in fields


def test_vent_only_room_gets_notify_but_no_hold_minutes():
    """CC-44: notify also gates the vent override notification (CC-38)."""
    fields = _fields(**{CONF_HAS_VENT_FAN: True})
    assert CONF_SETPOINT_HOLD_MINUTES not in fields
    assert CONF_NOTIFY_ON_HOLD_END in fields


def test_hold_fields_absent_for_fan_only_room():
    fields = _fields(**{CONF_HAS_FAN: True})
    assert CONF_SETPOINT_HOLD_MINUTES not in fields
    assert CONF_NOTIFY_ON_HOLD_END not in fields


def test_hold_minutes_optional_and_notify_defaults_on():
    fields = _fields(**{CONF_HAS_AC: True})
    assert isinstance(fields[CONF_SETPOINT_HOLD_MINUTES], vol.Optional)
    notify = fields[CONF_NOTIFY_ON_HOLD_END]
    assert isinstance(notify, vol.Required)
    assert notify.default() is True


@pytest.mark.parametrize(
    ("submitted", "saved"),
    [({}, None), ({CONF_SETPOINT_HOLD_MINUTES: 0}, 0)],
    ids=["blank", "zero"],
)
def test_save_loop_round_trips_hold_fields(submitted, saved):
    """Blank minutes save as None (models reads 60); an explicit 0 survives."""
    handler = RoomSubentryFlowHandler()
    handler._data = {CONF_ROOM_KEY: "office", CONF_HAS_AC: True}

    async def _next_step():
        return "sensors"

    handler.async_step_sensors = _next_step
    user_input = {CONF_NOTIFY_ON_HOLD_END: False, **submitted}
    assert asyncio.run(handler.async_step_devices(user_input)) == "sensors"
    assert handler._data[CONF_SETPOINT_HOLD_MINUTES] == saved
    assert handler._data[CONF_NOTIFY_ON_HOLD_END] is False
