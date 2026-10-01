"""
Tests for the profile humidity-number entities' ``async_added_to_hass``.

Finding (post-#77 review): an old profile whose stored preset has
``humidity=None`` and no restored value must stay a true no-op — it must NOT
get ``DEFAULT_HUMIDITY_TARGET`` silently written into the profile store, since
that would reset a user's live humidity target on the next apply.

Home Assistant is installed in this devcontainer, so ``number.py``/``models.py``
import directly here. A live ``hass``/event loop is avoided: these are plain
entities never added to a real entity platform, so ``async_get_last_number_
data`` (RestoreEntity's state-machine lookup) and ``_connect_profile_removal``
(dispatcher registration) are stubbed on the instance, and ``self.hass`` is a
``MagicMock`` stand-in. ``Entity.async_added_to_hass`` itself (the real
``super()`` call) is a documented no-op, so it needs no stubbing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.btoddb_room_climate_controller.const import DEVICE_VENT
from custom_components.btoddb_room_climate_controller.models import (
    DevicePreset,
    FanPreset,
    Profile,
)
from custom_components.btoddb_room_climate_controller.number import (
    ProfileFanHumidityNumber,
    ProfileVentHumidityNumber,
)


def _entry_for(profile):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.get_profile.return_value = profile
    return entry


def _drive_added_to_hass(entity, *, restored_value=None):
    """Run ``async_added_to_hass`` with the HA-platform internals stubbed out."""
    entity.hass = MagicMock()
    restored = (
        None if restored_value is None else MagicMock(native_value=restored_value)
    )
    entity.async_get_last_number_data = AsyncMock(return_value=restored)
    entity._connect_profile_removal = lambda: None
    asyncio.run(entity.async_added_to_hass())


# --- vent humidity (PR-13) ----------------------------------------------
def test_vent_humidity_old_profile_stays_none_and_is_not_synced():
    """humidity=None + no restored value: native_value stays None, no store write."""
    profile = Profile(
        id="01",
        name="Morning",
        room="bath",
        presets={DEVICE_VENT: DevicePreset(use=True, temp=70.0, humidity=None)},
    )
    entry = _entry_for(profile)
    entity = ProfileVentHumidityNumber(entry, profile)

    _drive_added_to_hass(entity, restored_value=None)

    assert entity._attr_native_value is None
    assert profile.presets[DEVICE_VENT].humidity is None
    entity.hass.async_create_task.assert_not_called()


def test_vent_humidity_restored_value_syncs_to_the_store():
    """A restored (user-set) value is written back to the profile store."""
    profile = Profile(
        id="01",
        name="Morning",
        room="bath",
        presets={DEVICE_VENT: DevicePreset(use=True, temp=70.0, humidity=None)},
    )
    entry = _entry_for(profile)
    entity = ProfileVentHumidityNumber(entry, profile)

    _drive_added_to_hass(entity, restored_value=52.0)

    assert entity._attr_native_value == 52.0
    assert profile.presets[DEVICE_VENT].humidity == 52.0
    entity.hass.async_create_task.assert_called_once()


def test_vent_humidity_seeds_from_an_existing_stored_preset():
    """A profile that already has a real stored humidity value seeds native_value."""
    profile = Profile(
        id="01",
        name="Morning",
        room="bath",
        presets={DEVICE_VENT: DevicePreset(use=True, temp=70.0, humidity=55.0)},
    )
    entry = _entry_for(profile)
    entity = ProfileVentHumidityNumber(entry, profile)

    _drive_added_to_hass(entity, restored_value=None)

    assert entity._attr_native_value == 55.0


# --- per-fan humidity (PR-14) --------------------------------------------
def test_fan_humidity_old_profile_stays_none_and_is_not_synced():
    """humidity=None + no restored value: native_value stays None, no store write."""
    profile = Profile(
        id="01",
        name="Morning",
        room="bath",
        fan_presets={"fan_a": FanPreset(use=True, temp=70.0, humidity=None)},
    )
    entry = _entry_for(profile)
    entity = ProfileFanHumidityNumber(entry, profile, "fan.a")

    _drive_added_to_hass(entity, restored_value=None)

    assert entity._attr_native_value is None
    assert profile.fan_presets["fan_a"].humidity is None
    entity.hass.async_create_task.assert_not_called()


def test_fan_humidity_restored_value_syncs_to_the_store():
    """A restored (user-set) value is written back to the profile store."""
    profile = Profile(
        id="01",
        name="Morning",
        room="bath",
        fan_presets={"fan_a": FanPreset(use=True, temp=70.0, humidity=None)},
    )
    entry = _entry_for(profile)
    entity = ProfileFanHumidityNumber(entry, profile, "fan.a")

    _drive_added_to_hass(entity, restored_value=48.0)

    assert entity._attr_native_value == 48.0
    assert profile.fan_presets["fan_a"].humidity == 48.0
    entity.hass.async_create_task.assert_called_once()
