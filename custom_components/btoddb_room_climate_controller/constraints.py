"""
Advisory clamping of a room's targets/offsets.

Watches a room's target and offset numbers; when an invalid combination is set
(heating ≥ cooling, an offset out of order, or an offset past a device limit) it
clamps the value and raises a persistent notification — non-blocking.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    DEVICE_COOLING,
    DEVICE_FAN,
    DEVICE_HEATING,
    DOMAIN,
    HUMIDITY_OFFSET_MAX,
    HUMIDITY_OFFSET_MIN,
    KEY_HIGH_OFFSET,
    KEY_HUMIDITY_HIGH_OFFSET,
    KEY_HUMIDITY_MEDIUM_OFFSET,
    KEY_HUMIDITY_TARGET,
    KEY_MEDIUM_OFFSET,
    KEY_TARGET,
    OFFSET_MAX,
    OFFSET_MIN,
)
from .models import Room, fan_slug, fan_target_key, room_uid

if TYPE_CHECKING:
    from .hub import RoomClimateConfigEntry

_LOGGER = logging.getLogger(__name__)
_INVALID = (None, "", STATE_UNKNOWN, STATE_UNAVAILABLE)
# Relative humidity can't exceed 100%, so neither can a target + its offset.
_HUMIDITY_CEILING = 100.0


class ConstraintsValidator:
    """Clamp a room's invalid target/offset combinations."""

    def __init__(
        self, hass: HomeAssistant, entry: RoomClimateConfigEntry, room: Room
    ) -> None:
        """Initialize the validator."""
        self.hass = hass
        self.entry = entry
        self.room = room
        self._unsub = None
        self._tracked: frozenset[str] = frozenset()
        self._busy = False
        # Values this validator has written and still expects to see echoed back
        # as state_changed events, keyed by entity_id. Used to ignore our own
        # clamp writes so two rules can't ping-pong the same number forever.
        self._pending: dict[str, list[float]] = {}

    @callback
    def async_start(self) -> None:
        """Subscribe to the room's target/offset numbers."""
        self._resubscribe()
        self.entry.async_on_unload(
            async_call_later(self.hass, 3, callback(lambda _now: self._resubscribe()))
        )
        self.entry.async_on_unload(self.async_stop)

    @callback
    def async_stop(self) -> None:
        """Unsubscribe."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _resubscribe(self) -> None:
        ids = self._tracked_ids()
        if ids == self._tracked:
            return
        self._tracked = ids
        if self._unsub:
            self._unsub()
            self._unsub = None
        if ids:
            self._unsub = async_track_state_change_event(
                self.hass, list(ids), self._on_change
            )

    def _tracked_ids(self) -> frozenset[str]:
        ids: set[str] = set()
        for device in self.room.devices:
            if device == DEVICE_FAN:
                continue
            for key in (
                KEY_TARGET[device],
                KEY_MEDIUM_OFFSET[device],
                KEY_HIGH_OFFSET[device],
            ):
                if eid := self._resolve(key, "number"):
                    ids.add(eid)
        if self.room.has_fan:
            for key in (KEY_MEDIUM_OFFSET["fan"], KEY_HIGH_OFFSET["fan"]):
                if eid := self._resolve(key, "number"):
                    ids.add(eid)
            for fan_eid in self.room.fan_entities:
                if eid := self._resolve(fan_target_key(fan_slug(fan_eid)), "number"):
                    ids.add(eid)
        if self._has_humidity:
            for key in (
                KEY_HUMIDITY_TARGET,
                KEY_HUMIDITY_MEDIUM_OFFSET,
                KEY_HUMIDITY_HIGH_OFFSET,
            ):
                if eid := self._resolve(key, "number"):
                    ids.add(eid)
        return frozenset(ids)

    @callback
    def _on_change(self, event: Event[EventStateChangedData]) -> None:
        self._resubscribe()
        # Drop the echo of one of our own clamp writes. Without this, a clamp
        # fires a state_changed for the number we just set, which re-runs the
        # rules; when two rules disagree on the same number (e.g. bounds wants
        # it lower, order wants it higher) they ping-pong forever and starve the
        # event loop. External changes (user/automations) are never suppressed.
        entity_id = event.data["entity_id"]
        if pending := self._pending.get(entity_id):
            new_state = event.data["new_state"]
            value: float | None = None
            if new_state is not None:
                try:
                    value = float(new_state.state)
                except TypeError, ValueError:
                    value = None
            if value is not None and value in pending:
                pending.remove(value)
                if not pending:
                    del self._pending[entity_id]
                return
        if self._busy:
            return
        self.hass.async_create_task(self._validate())

    async def _validate(self) -> None:
        self._busy = True
        try:
            if self.room.has_ac:
                await self._order(DEVICE_COOLING)
                await self._cooling_bounds()
            if self.room.has_heater:
                await self._order(DEVICE_HEATING)
                await self._heating_bounds()
            if self.room.has_fan:
                await self._order(DEVICE_FAN)
                await self._fan_bounds()
            if self._has_humidity:
                await self._humidity_order()
                await self._humidity_bounds()
            if self.room.has_ac and self.room.has_heater:
                await self._heating_below_cooling()
        finally:
            self._busy = False

    # -- rules ---------------------------------------------------------------
    async def _order(self, device: str) -> None:
        """High offset must exceed medium offset."""
        med = self._num(KEY_MEDIUM_OFFSET[device])
        high = self._num(KEY_HIGH_OFFSET[device])
        if med is None or high is None or med < high:
            return
        await self._clamp(
            KEY_HIGH_OFFSET[device],
            min(med + 1, OFFSET_MAX),
            f"{device.capitalize()} high offset must exceed the medium offset",
        )

    async def _cooling_bounds(self) -> None:
        target = self._num(KEY_TARGET[DEVICE_COOLING])
        high = self._num(KEY_HIGH_OFFSET[DEVICE_COOLING])
        ceiling = self.room.limits[DEVICE_COOLING]["max"]
        if target is None or high is None or target + high <= ceiling:
            return
        await self._clamp(
            KEY_HIGH_OFFSET[DEVICE_COOLING],
            self._bounded(ceiling - target),
            "Cooling high offset would exceed the cooling maximum",
        )

    async def _heating_bounds(self) -> None:
        target = self._num(KEY_TARGET[DEVICE_HEATING])
        high = self._num(KEY_HIGH_OFFSET[DEVICE_HEATING])
        floor = self.room.limits[DEVICE_HEATING]["min"]
        if target is None or high is None or target - high >= floor:
            return
        await self._clamp(
            KEY_HIGH_OFFSET[DEVICE_HEATING],
            self._bounded(target - floor),
            "Heating high offset would fall below the heating minimum",
        )

    async def _fan_bounds(self) -> None:
        targets = [
            t
            for eid in self.room.fan_entities
            if (t := self._num(fan_target_key(fan_slug(eid)))) is not None
        ]
        high = self._num(KEY_HIGH_OFFSET[DEVICE_FAN])
        ceiling = self.room.limits[DEVICE_FAN]["max"]
        if not targets or high is None:
            return
        worst = max(targets)
        if worst + high <= ceiling:
            return
        await self._clamp(
            KEY_HIGH_OFFSET[DEVICE_FAN],
            self._bounded(ceiling - worst),
            "Fan high offset would exceed the fan maximum",
        )

    async def _humidity_order(self) -> None:
        """Humidity high offset must exceed the medium offset."""
        med = self._num(KEY_HUMIDITY_MEDIUM_OFFSET)
        high = self._num(KEY_HUMIDITY_HIGH_OFFSET)
        if med is None or high is None or med < high:
            return
        await self._clamp(
            KEY_HUMIDITY_HIGH_OFFSET,
            min(med + 1, HUMIDITY_OFFSET_MAX),
            "Humidity high offset must exceed the medium offset",
        )

    async def _humidity_bounds(self) -> None:
        target = self._num(KEY_HUMIDITY_TARGET)
        high = self._num(KEY_HUMIDITY_HIGH_OFFSET)
        if target is None or high is None or target + high <= _HUMIDITY_CEILING:
            return
        capped = self._humidity_bounded(_HUMIDITY_CEILING - target)
        await self._clamp(
            KEY_HUMIDITY_HIGH_OFFSET,
            capped,
            "Humidity high offset would exceed 100%",
        )
        # Capping the high offset can leave the medium offset at or above it,
        # so re-apply the ordering rule (CC-16) against the capped value.
        med = self._num(KEY_HUMIDITY_MEDIUM_OFFSET)
        if med is None or med < capped:
            return
        await self._clamp(
            KEY_HUMIDITY_MEDIUM_OFFSET,
            max(capped - 1, HUMIDITY_OFFSET_MIN),
            "Humidity medium offset must stay below the high offset",
        )

    async def _heating_below_cooling(self) -> None:
        cooling = self._num(KEY_TARGET[DEVICE_COOLING])
        heating = self._num(KEY_TARGET[DEVICE_HEATING])
        if cooling is None or heating is None or heating < cooling:
            return
        await self._clamp(
            KEY_TARGET[DEVICE_HEATING],
            cooling - 1,
            "Heating target must stay below the cooling target",
        )

    # -- helpers -------------------------------------------------------------
    @property
    def _has_humidity(self) -> bool:
        """Whether the room has the humidity target/offset numbers at all."""
        return bool(self.room.has_fan and self.room.humidity_sensor)

    @staticmethod
    def _bounded(value: float) -> float:
        return max(OFFSET_MIN, min(OFFSET_MAX, round(value)))

    @staticmethod
    def _humidity_bounded(value: float) -> float:
        return max(HUMIDITY_OFFSET_MIN, min(HUMIDITY_OFFSET_MAX, round(value)))

    def _resolve(self, key: str, domain: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, room_uid(self.entry.entry_id, self.room.key, key)
        )

    def _num(self, key: str) -> float | None:
        eid = self._resolve(key, "number")
        if not eid:
            return None
        state = self.hass.states.get(eid)
        if state is None or state.state in _INVALID:
            return None
        try:
            return float(state.state)
        except TypeError, ValueError:
            return None

    async def _clamp(self, key: str, value: float, reason: str) -> None:
        eid = self._resolve(key, "number")
        if not eid:
            return
        # Already at the target value: nothing to write, and writing it would
        # leave a stale suppression entry (no state_changed would echo).
        if (current := self._num(key)) is not None and current == value:
            return
        _LOGGER.debug(
            "Room %s: clamping %s to %s (%s)", self.room.key, eid, value, reason
        )
        # Record the write so its echoed state_changed is ignored in _on_change.
        self._pending.setdefault(eid, []).append(float(value))
        await self.hass.services.async_call(
            "number", "set_value", {"entity_id": eid, "value": value}, blocking=True
        )
        persistent_notification.async_create(
            self.hass,
            f"{reason}. Adjusted to {value:g}.",
            title=f"Room Climate · {self.room.label}",
            notification_id=f"{DOMAIN}_{self.room.key}_{key}",
        )
