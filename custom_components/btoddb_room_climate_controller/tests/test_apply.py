"""
Tests for ``apply.py``'s vent/per-fan humidity preset writes (issue #77).

Home Assistant is installed in this devcontainer (see ``test_humidity_triggers_
evaluation.py``'s docstring), so ``apply.py``/``models.py`` import directly
here. A live ``hass``/event loop is avoided: ``resolve_room_entity`` is
monkeypatched to bypass the entity registry (mirrors
``test_constraints_humidity.py``'s approach), and ``hass.services`` is a tiny
stand-in that records calls.

This covers only the apply.py writes themselves (humidity set -> the right
service call; humidity None -> no call; no humidity_sensor -> no call even
with a stale resolved entity). The full copy-room -> store -> apply round
trip through the websocket API's live-number reads is not covered here — it
would need disproportionate HA mocking for this fix; see PR notes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from custom_components.btoddb_room_climate_controller import apply as apply_module
from custom_components.btoddb_room_climate_controller.models import (
    DevicePreset,
    FanPreset,
    Profile,
    Room,
)


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": False,
        "has_heater": False,
        "has_fan": True,
        "combined": False,
        "ac_climate": None,
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
    return Room(**defaults)


def _profile(room_key, **overrides):
    profile = Profile(id="01", name="Test", room=room_key)
    for key, value in overrides.items():
        setattr(profile, key, value)
    return profile


class _StubServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data, blocking=True):  # noqa: ARG002
        self.calls.append((domain, service, data))


class _StubHass:
    def __init__(self):
        self.services = _StubServices()


def _make_entry(room, hass):
    runtime_data = MagicMock()
    runtime_data.hass = hass
    runtime_data.room_by_key = lambda _key: room
    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "entry1"
    return entry


def _run_apply(entry, profile):
    with patch.object(
        apply_module,
        "resolve_room_entity",
        lambda _h, _e, _r, key, domain: f"{domain}.{key}",
    ):
        asyncio.run(apply_module.async_apply_profile(entry, profile, force=True))


# -- vent humidity (generic device loop, apply.py ~119-133) ------------------
def test_apply_writes_vent_humidity_when_preset_has_a_value():
    """A vent preset with a humidity value calls number.set_value with it."""
    room = _room()
    hass = _StubHass()
    entry = _make_entry(room, hass)
    profile = _profile(
        room.key,
        presets={"vent_fan": DevicePreset(use=True, temp=70.0, humidity=55.0)},
    )

    _run_apply(entry, profile)

    assert (
        "number",
        "set_value",
        {"entity_id": "number.vent_fan_humidity_target", "value": 55.0},
    ) in (hass.services.calls)


def test_apply_skips_vent_humidity_write_when_preset_humidity_is_none():
    """A pre-#77 profile with humidity=None is a true no-op for humidity."""
    room = _room()
    hass = _StubHass()
    entry = _make_entry(room, hass)
    profile = _profile(
        room.key,
        presets={"vent_fan": DevicePreset(use=True, temp=70.0, humidity=None)},
    )

    _run_apply(entry, profile)

    assert all(
        call[2].get("entity_id") != "number.vent_fan_humidity_target"
        for call in hass.services.calls
    )


def test_apply_skips_vent_humidity_write_without_a_humidity_sensor():
    """No room.humidity_sensor: skip the humidity write even if resolved (finding 8)."""
    room = _room(humidity_sensor=None)
    hass = _StubHass()
    entry = _make_entry(room, hass)
    profile = _profile(
        room.key,
        presets={"vent_fan": DevicePreset(use=True, temp=70.0, humidity=55.0)},
    )

    _run_apply(entry, profile)

    assert all(
        call[2].get("entity_id") != "number.vent_fan_humidity_target"
        for call in hass.services.calls
    )


# -- per-fan humidity (_apply_fan_presets, apply.py ~183-193) -----------------
def test_apply_writes_fan_humidity_when_preset_has_a_value():
    """A fan preset with a humidity value calls number.set_value with it."""
    room = _room()
    hass = _StubHass()
    entry = _make_entry(room, hass)
    profile = _profile(
        room.key,
        fan_presets={
            "fan_office_tower": FanPreset(use=True, temp=70.0, humidity=48.0),
        },
    )

    _run_apply(entry, profile)

    calls = [c for c in hass.services.calls if "humidity" in c[2].get("entity_id", "")]
    expected = (
        "number",
        "set_value",
        {"entity_id": "number.fan_humidity_target__fan_office_tower", "value": 48.0},
    )
    assert expected in calls


def test_apply_skips_fan_humidity_write_when_preset_humidity_is_none():
    """A fan preset with humidity=None never calls set_value for humidity."""
    room = _room()
    hass = _StubHass()
    entry = _make_entry(room, hass)
    profile = _profile(
        room.key,
        fan_presets={
            "fan_office_tower": FanPreset(use=True, temp=70.0, humidity=None),
        },
    )

    _run_apply(entry, profile)

    assert all(
        "humidity" not in call[2].get("entity_id", "") for call in hass.services.calls
    )


def test_apply_skips_fan_humidity_write_without_a_humidity_sensor():
    """No room.humidity_sensor: skip the per-fan humidity write (finding 8)."""
    room = _room(humidity_sensor=None)
    hass = _StubHass()
    entry = _make_entry(room, hass)
    profile = _profile(
        room.key,
        fan_presets={
            "fan_office_tower": FanPreset(use=True, temp=70.0, humidity=48.0),
        },
    )

    _run_apply(entry, profile)

    assert all(
        "humidity" not in call[2].get("entity_id", "") for call in hass.services.calls
    )
