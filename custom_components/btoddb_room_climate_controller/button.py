"""Button platform: the per-room "Resume control" action (CC-45)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity

from .const import KEY_RESUME_CONTROL
from .entity import room_device_info
from .models import Room, room_uid

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .hub import RoomClimateConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a Resume control button per room with a holdable device."""
    hub = entry.runtime_data

    for room in hub.rooms.values():
        if not (room.has_ac or room.has_heater or room.has_vent_fan):
            continue
        _LOGGER.debug("[room=%s] button: Resume control", room.key)
        async_add_entities(
            [ResumeControlButton(entry, room)], config_subentry_id=room.room_id
        )


class ResumeControlButton(ButtonEntity):
    """Ends every setpoint hold and the vent override for a room (CC-45)."""

    _attr_has_entity_name = True
    _attr_name = "Resume control"
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, entry: RoomClimateConfigEntry, room: Room) -> None:
        """Initialize the button."""
        self._entry = entry
        self._room = room
        self._attr_unique_id = room_uid(entry.entry_id, room.key, KEY_RESUME_CONTROL)
        self._attr_device_info = room_device_info(entry, room)

    async def async_press(self) -> None:
        """Resume climate control now; never notifies."""
        # Controllers are created after platform setup, so look up lazily.
        controller = self._entry.runtime_data.controllers.get(self._room.key)
        if controller is None:
            _LOGGER.warning(
                "[room=%s] Resume control pressed before the controller started",
                self._room.key,
            )
            return
        controller.async_resume_control("button")
