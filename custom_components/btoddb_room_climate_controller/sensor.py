"""
Sensor platform: per-room temperature/humidity/power mirrors + outdoor temp.

These wrap the user-configured source sensors so the rest of the system (and
dashboards) reference a stable ``sensor.<room>_room_*`` entity even if the
underlying device sensor is swapped out later. A single hub-level
``Outdoor Temperature`` mirror does the same for the outdoor source, so graphs
can re-point the real weather sensor in one place. Rooms with a vent fan also
get a ``Vent fan override`` status sensor (CC-40), fed by the controller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_OUTDOOR_SENSOR,
    KEY_OUTDOOR_TEMPERATURE,
    KEY_ROOM_HUMIDITY,
    KEY_ROOM_POWER,
    KEY_ROOM_TEMPERATURE,
    KEY_VENT_OVERRIDE,
    SIGNAL_VENT_OVERRIDE,
)
from .entity import hub_identifier, room_device_info
from .models import Room, room_uid

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .hub import RoomClimateConfigEntry


@dataclass(frozen=True)
class _SensorSpec:
    key: str
    name: str
    source: str
    device_class: SensorDeviceClass
    icon: str


def _room_specs(room: Room) -> list[_SensorSpec]:
    specs: list[_SensorSpec] = []
    if room.temperature_sensor:
        specs.append(
            _SensorSpec(
                KEY_ROOM_TEMPERATURE,
                "Temperature",
                room.temperature_sensor,
                SensorDeviceClass.TEMPERATURE,
                "mdi:thermometer",
            )
        )
    if room.humidity_sensor:
        specs.append(
            _SensorSpec(
                KEY_ROOM_HUMIDITY,
                "Humidity",
                room.humidity_sensor,
                SensorDeviceClass.HUMIDITY,
                "mdi:water-percent",
            )
        )
    if room.power_sensor:
        specs.append(
            _SensorSpec(
                KEY_ROOM_POWER,
                "Power",
                room.power_sensor,
                SensorDeviceClass.POWER,
                "mdi:flash",
            )
        )
    return specs


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up per-room mirror sensors plus the hub outdoor-temperature mirror."""
    hub = entry.runtime_data
    for room in hub.rooms.values():
        specs = _room_specs(room)
        if specs:
            _LOGGER.debug(
                "[room=%s] sensor: %s",
                room.key,
                ", ".join(f"{s.name} ({s.source})" for s in specs),
            )
            async_add_entities(
                [RoomMirrorSensor(entry, room, spec) for spec in specs],
                config_subentry_id=room.room_id,
            )
        if room.has_vent_fan and room.vent_fan_entity:
            async_add_entities(
                [VentOverrideSensor(entry, room)],
                config_subentry_id=room.room_id,
            )

    outdoor_source = entry.data.get(CONF_OUTDOOR_SENSOR)
    if outdoor_source:
        async_add_entities([OutdoorMirrorSensor(entry, outdoor_source)])


class _MirrorSensor(SensorEntity):
    """Mirrors a source sensor's value, unit, and availability."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    _source: str

    async def async_added_to_hass(self) -> None:
        """Subscribe to the source and seed the initial value."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source], self._handle_source
            )
        )
        self._update_from_source()

    @callback
    def _handle_source(self, _event: Event[EventStateChangedData]) -> None:
        self._update_from_source()
        self.async_write_ha_state()

    @callback
    def _update_from_source(self) -> None:
        state = self.hass.states.get(self._source)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._attr_available = False
            return
        self._attr_available = True
        try:
            self._attr_native_value = float(state.state)
        except TypeError, ValueError:
            self._attr_native_value = state.state
        self._attr_native_unit_of_measurement = state.attributes.get(
            "unit_of_measurement"
        )


class RoomMirrorSensor(_MirrorSensor):
    """A room's temperature/humidity/power abstraction sensor."""

    def __init__(
        self, entry: RoomClimateConfigEntry, room: Room, spec: _SensorSpec
    ) -> None:
        """Initialize the mirror."""
        self._source = spec.source
        self._attr_unique_id = room_uid(entry.entry_id, room.key, spec.key)
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class
        self._attr_device_info = room_device_info(entry, room)


class OutdoorMirrorSensor(_MirrorSensor):
    """Hub-level abstraction of the outdoor temperature source."""

    _attr_name = "Outdoor Temperature"
    _attr_icon = "mdi:thermometer"
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, entry: RoomClimateConfigEntry, source: str) -> None:
        """Initialize the outdoor mirror, attached to the hub device."""
        self._source = source
        self._attr_unique_id = f"{entry.entry_id}_{KEY_OUTDOOR_TEMPERATURE}"
        self._attr_device_info = DeviceInfo(
            identifiers={hub_identifier(entry.entry_id)}
        )


class VentOverrideSensor(SensorEntity):
    """
    A room's vent fan manual-override status (CC-40): ``none``/``on``/``off``.

    Fed by the room controller over a per-room dispatcher signal. Not
    restored: the override is in-memory, so after a restart it is ``none``.
    """

    _attr_has_entity_name = True
    _attr_name = "Vent fan override"
    _attr_icon = "mdi:fan-clock"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, entry: RoomClimateConfigEntry, room: Room) -> None:
        """Initialize the status sensor, attached to the room device."""
        self._attr_options = ["none", "on", "off"]
        self._signal = f"{SIGNAL_VENT_OVERRIDE}_{entry.entry_id}_{room.key}"
        self._attr_unique_id = room_uid(entry.entry_id, room.key, KEY_VENT_OVERRIDE)
        self._attr_device_info = room_device_info(entry, room)
        self._attr_native_value = "none"
        self._attr_extra_state_attributes = {"until": None}

    async def async_added_to_hass(self) -> None:
        """Follow the controller's override updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._signal, self._handle_override)
        )

    @callback
    def _handle_override(self, state: str, until: datetime | None) -> None:
        self._attr_native_value = state
        self._attr_extra_state_attributes = {
            "until": until.isoformat() if until else None
        }
        self.async_write_ha_state()
