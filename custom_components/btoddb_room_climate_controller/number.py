"""Number platform: room targets/offsets and profile preset temperatures."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DEFAULT_HIGH_OFFSET,
    DEFAULT_MEDIUM_OFFSET,
    DEVICE_FAN,
    DEVICE_ICONS,
    KEY_HIGH_OFFSET,
    KEY_MEDIUM_OFFSET,
    KEY_PROFILE_PRESET,
    KEY_TARGET,
    LOGGER_PROFILE,
    LOGGER_SETTINGS,
    OFFSET_MAX,
    OFFSET_MIN,
    SIGNAL_ADD_PROFILE_ENTITIES,
    TEMP_UNIT,
)
from .entity import ProfileRemovalMixin, profile_device_info, room_device_info
from .models import (
    Profile,
    Room,
    fan_slug,
    fan_target_key,
    profile_fan_temp_key,
    profile_uid,
    room_uid,
)


def _fan_label(entity_id: str) -> str:
    """Human-readable label for a fan entity id (object_id titled)."""
    return entity_id.rsplit(".", maxsplit=1)[-1].replace("_", " ").title()


if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .hub import RoomClimateConfigEntry

_LOGGER = logging.getLogger(__name__)
_SETTINGS_LOGGER = logging.getLogger(LOGGER_SETTINGS)
_PROFILE_LOGGER = logging.getLogger(LOGGER_PROFILE)


@dataclass(frozen=True)
class _NumberSpec:
    key: str
    name: str
    icon: str
    minimum: float
    maximum: float
    step: float
    default: float


def _room_specs(room: Room) -> list[_NumberSpec]:
    specs: list[_NumberSpec] = []
    for device in room.devices:
        limits = room.limits[device]
        if device == DEVICE_FAN:
            # Shared medium/high offsets (one pair per room)...
            specs.append(
                _NumberSpec(
                    key=KEY_MEDIUM_OFFSET[device],
                    name=f"{device.capitalize()} medium offset",
                    icon="mdi:fan-speed-2",
                    minimum=OFFSET_MIN,
                    maximum=OFFSET_MAX,
                    step=1,
                    default=DEFAULT_MEDIUM_OFFSET,
                )
            )
            specs.append(
                _NumberSpec(
                    key=KEY_HIGH_OFFSET[device],
                    name=f"{device.capitalize()} high offset",
                    icon="mdi:fan-speed-3",
                    minimum=OFFSET_MIN,
                    maximum=OFFSET_MAX,
                    step=1,
                    default=DEFAULT_HIGH_OFFSET,
                )
            )
            # ...and a per-fan target temp.
            specs.extend(
                _NumberSpec(
                    key=fan_target_key(fan_slug(eid)),
                    name=f"{_fan_label(eid)} target",
                    icon=DEVICE_ICONS[DEVICE_FAN],
                    minimum=limits["min"],
                    maximum=limits["max"],
                    step=1,
                    default=limits["min"],
                )
                for eid in room.fan_entities
            )
            continue
        specs.append(
            _NumberSpec(
                key=KEY_TARGET[device],
                name={
                    "cooling": "Cooling target",
                    "heating": "Heating target",
                }[device],
                icon=DEVICE_ICONS[device],
                minimum=limits["min"],
                maximum=limits["max"],
                step=1,
                default=limits["min"],
            )
        )
        specs.append(
            _NumberSpec(
                key=KEY_MEDIUM_OFFSET[device],
                name=f"{device.capitalize()} medium offset",
                icon="mdi:fan-speed-2",
                minimum=OFFSET_MIN,
                maximum=OFFSET_MAX,
                step=1,
                default=DEFAULT_MEDIUM_OFFSET,
            )
        )
        specs.append(
            _NumberSpec(
                key=KEY_HIGH_OFFSET[device],
                name=f"{device.capitalize()} high offset",
                icon="mdi:fan-speed-3",
                minimum=OFFSET_MIN,
                maximum=OFFSET_MAX,
                step=1,
                default=DEFAULT_HIGH_OFFSET,
            )
        )
    return specs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up room and profile number entities."""
    hub = entry.runtime_data

    for room in hub.rooms.values():
        specs = _room_specs(room)
        _LOGGER.debug(
            "[room=%s] number: %s",
            room.key,
            ", ".join(f"{s.name} [{s.minimum:.0f}-{s.maximum:.0f}]" for s in specs),
        )
        async_add_entities(
            [RoomNumber(entry, room, spec) for spec in specs],
            config_subentry_id=room.room_id,
        )

    @callback
    def _add_profile(profile: Profile) -> None:
        room = hub.room_by_key(profile.room)
        if room is None:
            return
        entities: list[RestoreNumber] = [
            ProfilePresetNumber(entry, profile, room, device)
            for device in room.devices
            if device != DEVICE_FAN
        ]
        entities.extend(
            ProfileFanPresetNumber(entry, profile, room, eid)
            for eid in room.fan_entities
        )
        if entities:
            async_add_entities(entities, config_subentry_id=room.room_id)

    for profile in hub.profiles:
        _add_profile(profile)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ADD_PROFILE_ENTITIES, _add_profile)
    )


class RoomNumber(RestoreNumber):
    """A user-settable per-room number (target temp or fan-speed offset)."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self, entry: RoomClimateConfigEntry, room: Room, spec: _NumberSpec
    ) -> None:
        """Initialize the number."""
        self._attr_unique_id = room_uid(entry.entry_id, room.key, spec.key)
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = TEMP_UNIT
        self._attr_device_info = room_device_info(entry, room)
        self._default = spec.default
        self._room_key = room.key

    async def async_added_to_hass(self) -> None:
        """Restore the last value or fall back to the default."""
        await super().async_added_to_hass()
        data = await self.async_get_last_number_data()
        if data is not None and data.native_value is not None:
            self._attr_native_value = data.native_value
        else:
            self._attr_native_value = self._default

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        old = self._attr_native_value
        self._attr_native_value = value
        self.async_write_ha_state()
        if old != value:
            _SETTINGS_LOGGER.info(
                "[room=%s] %s → %s°F", self._room_key, self._attr_name, int(value)
            )


class ProfilePresetNumber(ProfileRemovalMixin, RestoreNumber):
    """A profile's per-device preset target temperature."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        entry: RoomClimateConfigEntry,
        profile: Profile,
        room: Room,
        device: str,
    ) -> None:
        """Initialize the preset number."""
        self._entry = entry
        self._profile_id = profile.id
        self._device = device
        limits = room.limits[device]
        self._attr_unique_id = profile_uid(
            entry.entry_id, profile.id, KEY_PROFILE_PRESET[device]
        )
        self._attr_name = f"{device.capitalize()} target"
        self._attr_icon = DEVICE_ICONS[device]
        self._attr_native_min_value = limits["min"]
        self._attr_native_max_value = limits["max"]
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = TEMP_UNIT
        self._attr_device_info = profile_device_info(entry, profile)
        self._default = profile.presets.get(device)

    async def async_added_to_hass(self) -> None:
        """Restore the last value, defaulting to the profile's stored preset."""
        await super().async_added_to_hass()
        self._connect_profile_removal()
        data = await self.async_get_last_number_data()
        if data is not None and data.native_value is not None:
            self._attr_native_value = data.native_value
        elif self._default is not None:
            self._attr_native_value = self._default.temp
        else:
            self._attr_native_value = self._attr_native_min_value
        self._sync_to_store()

    async def async_set_native_value(self, value: float) -> None:
        """Update the preset temp and persist to the profile store."""
        old = self._attr_native_value
        self._attr_native_value = value
        self.async_write_ha_state()
        self._sync_to_store()
        if old != value:
            profile = self._entry.runtime_data.get_profile(self._profile_id)
            if profile is not None:
                _PROFILE_LOGGER.info(
                    "[room=%s profile=%s] Profile preset edited: %s target → %s°F",
                    profile.room,
                    profile.name,
                    self._device,
                    int(value),
                )

    @callback
    def _sync_to_store(self) -> None:
        hub = self._entry.runtime_data
        profile = hub.get_profile(self._profile_id)
        if profile is None or self._attr_native_value is None:
            return
        preset = profile.ensure_preset(self._device)
        if preset.temp != self._attr_native_value:
            preset.temp = float(self._attr_native_value)
            self.hass.async_create_task(hub.async_save())


class ProfileFanPresetNumber(ProfileRemovalMixin, RestoreNumber):
    """A profile's per-fan preset target temperature."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        entry: RoomClimateConfigEntry,
        profile: Profile,
        room: Room,
        entity_id: str,
    ) -> None:
        """Initialize the per-fan preset number."""
        self._entry = entry
        self._profile_id = profile.id
        self._slug = fan_slug(entity_id)
        self._label = _fan_label(entity_id)
        limits = room.limits[DEVICE_FAN]
        self._attr_unique_id = profile_uid(
            entry.entry_id, profile.id, profile_fan_temp_key(self._slug)
        )
        self._attr_name = f"{self._label} target"
        self._attr_icon = DEVICE_ICONS[DEVICE_FAN]
        self._attr_native_min_value = limits["min"]
        self._attr_native_max_value = limits["max"]
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = TEMP_UNIT
        self._attr_device_info = profile_device_info(entry, profile)
        self._default = profile.fan_presets.get(self._slug)

    async def async_added_to_hass(self) -> None:
        """Restore the last value, defaulting to the profile's stored preset."""
        await super().async_added_to_hass()
        self._connect_profile_removal()
        data = await self.async_get_last_number_data()
        if data is not None and data.native_value is not None:
            self._attr_native_value = data.native_value
        elif self._default is not None:
            self._attr_native_value = self._default.temp
        else:
            self._attr_native_value = self._attr_native_min_value
        self._sync_to_store()

    async def async_set_native_value(self, value: float) -> None:
        """Update the preset temp and persist to the profile store."""
        old = self._attr_native_value
        self._attr_native_value = value
        self.async_write_ha_state()
        self._sync_to_store()
        if old != value:
            profile = self._entry.runtime_data.get_profile(self._profile_id)
            if profile is not None:
                _PROFILE_LOGGER.info(
                    "[room=%s profile=%s] Profile preset edited: %s target → %s°F",
                    profile.room,
                    profile.name,
                    self._label,
                    int(value),
                )

    @callback
    def _sync_to_store(self) -> None:
        hub = self._entry.runtime_data
        profile = hub.get_profile(self._profile_id)
        if profile is None or self._attr_native_value is None:
            return
        preset = profile.ensure_fan_preset(self._slug)
        if preset.temp != self._attr_native_value:
            preset.temp = float(self._attr_native_value)
            self.hass.async_create_task(hub.async_save())
