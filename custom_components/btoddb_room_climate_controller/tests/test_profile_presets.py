"""
Profile preset model-level behaviors.

``Profile.ensure_preset`` backfills a preset for a device added after creation;
the rest cover PR-13/PR-14 (vent + per-fan humidity-target presets): seeding via
``with_defaults``, and ``to_dict``/``from_dict`` round-tripping — including a
stored profile dict from before #77 that has no ``"humidity"`` keys at all.
"""

from __future__ import annotations

from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.models import (
    DevicePreset,
    FanPreset,
    Profile,
)


def _profile(**presets: DevicePreset) -> Profile:
    return Profile(id="01", name="Morning", room="main_floor", presets=presets)


def _room(**overrides):
    """Build a room; has a vent fan + one standalone fan by default."""
    defaults = {
        "room_id": "sub1",
        "key": "bath",
        "label": "Bath",
        "area_id": None,
        "has_ac": True,
        "has_heater": False,
        "has_fan": True,
        "combined": False,
        "ac_climate": "climate.bath_ac",
        "heater_climate": None,
        "fan_entities": ("fan.bath_tower",),
        "ac_fan_entity": None,
        "heater_fan_entity": None,
        "ac_power_switch": None,
        "heater_power_switch": None,
        "temperature_sensor": "sensor.bath_temp",
        "humidity_sensor": "sensor.bath_humidity",
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
        "vent_fan_entity": "switch.bath_vent",
    }
    defaults.update(overrides)
    return models.Room(**defaults)


def test_ensure_preset_creates_missing_default() -> None:
    profile = _profile(cooling=DevicePreset(use=True, temp=75.0))

    preset = profile.ensure_preset("fan")

    assert preset is profile.presets["fan"]
    assert preset.use is False
    assert preset.temp == 0.0


# ---------------------------------------------------------------------------
# PR-13/PR-14: with_defaults seeds vent + per-fan humidity presets
# ---------------------------------------------------------------------------
def test_with_defaults_seeds_vent_preset_use_off_and_temp_at_min() -> None:
    room = _room()

    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)

    vent = profile.presets["vent_fan"]
    assert vent.use is False
    assert vent.temp == room.limits["vent_fan"]["min"]


def test_with_defaults_seeds_vent_humidity_when_room_has_humidity_sensor() -> None:
    room = _room()

    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)

    assert profile.presets["vent_fan"].humidity == 60


def test_with_defaults_leaves_vent_humidity_none_without_humidity_sensor() -> None:
    room = _room(humidity_sensor=None)

    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)

    assert profile.presets["vent_fan"].humidity is None


def test_with_defaults_seeds_fan_humidity_when_room_has_humidity_sensor() -> None:
    room = _room()

    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)

    fan_preset = profile.fan_presets[models.fan_slug("fan.bath_tower")]
    assert fan_preset.humidity == 60


def test_with_defaults_leaves_fan_humidity_none_without_humidity_sensor() -> None:
    room = _room(humidity_sensor=None)

    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)

    fan_preset = profile.fan_presets[models.fan_slug("fan.bath_tower")]
    assert fan_preset.humidity is None


# ---------------------------------------------------------------------------
# PR-13/PR-14: to_dict / from_dict humidity round-trip
# ---------------------------------------------------------------------------
def test_vent_preset_humidity_round_trips_through_to_dict_from_dict() -> None:
    room = _room()
    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)
    profile.presets["vent_fan"].use = True
    profile.presets["vent_fan"].humidity = 45.0

    restored = Profile.from_dict(profile.to_dict())

    assert restored.presets["vent_fan"].humidity == 45.0
    assert restored.presets["vent_fan"].use is True


def test_fan_preset_humidity_round_trips_through_to_dict_from_dict() -> None:
    room = _room()
    profile = Profile.with_defaults(profile_id="01", name="Morning", room=room)
    slug = models.fan_slug("fan.bath_tower")
    profile.fan_presets[slug].humidity = 70.0

    restored = Profile.from_dict(profile.to_dict())

    assert restored.fan_presets[slug].humidity == 70.0


def test_from_dict_without_humidity_keys_loads_with_humidity_none() -> None:
    """A profile dict stored before #77 has no "humidity" key at all."""
    stored = {
        "id": "01",
        "name": "Morning",
        "room": "bath",
        "presets": {
            "cooling": {"use": True, "temp": 72.0},
            "vent_fan": {"use": False, "temp": 65.0},
        },
        "fan_presets": {
            "fan_bath_tower": {"use": True, "temp": 70.0, "reverse": False},
        },
    }

    profile = Profile.from_dict(stored)

    assert profile.presets["vent_fan"].humidity is None
    assert profile.presets["cooling"].humidity is None
    assert profile.fan_presets["fan_bath_tower"].humidity is None

    # And it serializes/re-loads cleanly afterward.
    round_tripped = Profile.from_dict(profile.to_dict())
    assert round_tripped.presets["vent_fan"].humidity is None
    assert round_tripped.fan_presets["fan_bath_tower"].humidity is None


def test_device_preset_default_humidity_is_none() -> None:
    assert DevicePreset().humidity is None


def test_fan_preset_default_humidity_is_none() -> None:
    assert FanPreset().humidity is None
