"""
Apply a profile's presets to its room's live entities.

This only writes the room's live ``number``/``switch`` entities; the room's
``RoomController`` then reacts and drives the hardware.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON

from .const import (
    DEVICE_FAN,
    KEY_AC_FAN_ONLY,
    KEY_MANUAL_MODE,
    KEY_TARGET,
    KEY_USE,
    LOGGER_PROFILE,
)
from .entity import resolve_room_entity
from .models import fan_reverse_key, fan_slug, fan_target_key, fan_use_key

if TYPE_CHECKING:
    from .hub import RoomClimateConfigEntry
    from .models import Profile, Room

_PROFILE_LOGGER = logging.getLogger(LOGGER_PROFILE)


def _describe_settings(room: Room, profile: Profile) -> str:
    """Render a profile's presets (non-fan devices + each fan) for the log line."""
    parts = [
        f"{device} {'on' if p.use else 'off'}@{int(p.temp)}°F"
        for device in room.devices
        if device != DEVICE_FAN and (p := profile.presets.get(device)) is not None
    ]
    if room.has_fan:
        for eid in room.fan_entities:
            fp = profile.fan_presets.get(fan_slug(eid))
            if fp is None:
                continue
            parts.append(
                f"fan[{eid}] {'on' if fp.use else 'off'}@{int(fp.temp)}°F"
                f"{' rev' if fp.reverse else ''}"
            )
    return ", ".join(parts)


async def async_apply_profile(
    entry: RoomClimateConfigEntry, profile: Profile, *, force: bool = False
) -> None:
    """
    Copy a profile's presets onto its room's live entities.

    ``force=False`` (scheduled fire) skips when manual mode is on, mirroring the
    old blueprint. ``force=True`` (explicit "apply now") always applies.
    """
    hass = entry.runtime_data.hass
    room = entry.runtime_data.room_by_key(profile.room)
    if room is None:
        return

    if not force:
        manual = resolve_room_entity(
            hass, entry.entry_id, room.key, KEY_MANUAL_MODE, "switch"
        )
        if manual and hass.states.is_state(manual, STATE_ON):
            _PROFILE_LOGGER.info(
                "[room=%s profile=%s] Profile '%s' skipped: manual mode active",
                room.key,
                profile.name,
                profile.name,
            )
            return

    _PROFILE_LOGGER.info(
        "[room=%s profile=%s] Profile '%s' applied (%s): %s",
        room.key,
        profile.name,
        profile.name,
        "explicit" if force else "scheduled",
        _describe_settings(room, profile) or "no presets",
    )
    for device in room.devices:
        if device == DEVICE_FAN:
            continue
        preset = profile.presets.get(device)
        if preset is None:
            continue
        if use_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, KEY_USE[device], "switch"
        ):
            await hass.services.async_call(
                "switch",
                "turn_on" if preset.use else "turn_off",
                {"entity_id": use_eid},
                blocking=True,
            )
        if target_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, KEY_TARGET[device], "number"
        ):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": target_eid, "value": preset.temp},
                blocking=True,
            )

    if (
        room.has_ac
        and room.ac_fan_only
        and (
            ov_eid := resolve_room_entity(
                hass, entry.entry_id, room.key, KEY_AC_FAN_ONLY, "switch"
            )
        )
    ):
        await hass.services.async_call(
            "switch",
            "turn_on" if profile.fan_override else "turn_off",
            {"entity_id": ov_eid},
            blocking=True,
        )

    if room.has_fan:
        await _apply_fan_presets(entry, room, profile)


async def _apply_fan_presets(
    entry: RoomClimateConfigEntry, room: Room, profile: Profile
) -> None:
    """Apply each fan's use/target/reverse preset to its room live entities."""
    hass = entry.runtime_data.hass
    for eid in room.fan_entities:
        slug = fan_slug(eid)
        fp = profile.fan_presets.get(slug)
        if fp is None:
            continue
        if use_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, fan_use_key(slug), "switch"
        ):
            await hass.services.async_call(
                "switch",
                "turn_on" if fp.use else "turn_off",
                {"entity_id": use_eid},
                blocking=True,
            )
        if target_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, fan_target_key(slug), "number"
        ):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": target_eid, "value": fp.temp},
                blocking=True,
            )
        if rev_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, fan_reverse_key(slug), "switch"
        ):
            await hass.services.async_call(
                "switch",
                "turn_on" if fp.reverse else "turn_off",
                {"entity_id": rev_eid},
                blocking=True,
            )
