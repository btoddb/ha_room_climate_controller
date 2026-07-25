"""Websocket commands backing the Lovelace card (synchronous request/response)."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .apply import async_apply_profile
from .const import (
    DEVICE_FAN,
    DOMAIN,
    KEY_AC_FAN_ONLY,
    KEY_GRAPH_TIME_RANGE,
    KEY_HEATER_FAN_ONLY,
    KEY_HIGH_OFFSET,
    KEY_HUMIDITY_HIGH_OFFSET,
    KEY_HUMIDITY_MEDIUM_OFFSET,
    KEY_HUMIDITY_TARGET,
    KEY_MANUAL_MODE,
    KEY_MEDIUM_OFFSET,
    KEY_OUTDOOR_TEMPERATURE,
    KEY_PROFILE_ENABLED,
    KEY_PROFILE_FAN_OVERRIDE,
    KEY_PROFILE_PRESET,
    KEY_PROFILE_TIME,
    KEY_PROFILE_USE,
    KEY_ROOM_HUMIDITY,
    KEY_ROOM_POWER,
    KEY_ROOM_TEMPERATURE,
    KEY_TARGET,
    KEY_USE,
    LOGGER_PROFILE,
    SIGNAL_ADD_PROFILE_ENTITIES,
    SIGNAL_REMOVE_PROFILE,
)
from .entity import (
    async_assign_areas,
    fan_supports_direction,
    resolve_hub_entity,
    resolve_profile_entity,
    resolve_room_entity,
)
from .models import (
    Profile,
    Room,
    fan_reverse_key,
    fan_slug,
    fan_target_key,
    fan_use_key,
    find_time_conflict,
    format_profile_id,
    next_profile_id,
    normalize_time_hhmm,
    profile_fan_reverse_key,
    profile_fan_temp_key,
    profile_fan_use_key,
)

if TYPE_CHECKING:
    from .hub import RoomClimateConfigEntry

_PROFILE_LOGGER = logging.getLogger(LOGGER_PROFILE)


def _fan_label(hass: HomeAssistant, entity_id: str) -> str:
    """Human-friendly label for a fan entity (falls back to a titled object id)."""
    state = hass.states.get(entity_id)
    if state and state.name:
        return state.name
    return entity_id.rsplit(".", maxsplit=1)[-1].replace("_", " ").title()


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Register all Room Climate websocket commands."""
    for command in (
        ws_list_rooms,
        ws_list_profiles,
        ws_create_profile,
        ws_delete_profile,
        ws_rename_profile,
        ws_set_room,
        ws_apply_profile,
    ):
        websocket_api.async_register_command(hass, command)


@callback
def _get_entry(hass: HomeAssistant) -> RoomClimateConfigEntry | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


def _require_entry(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> RoomClimateConfigEntry | None:
    entry = _get_entry(hass)
    if entry is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Room Climate is not set up"
        )
    return entry


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def _serialize_room(
    hass: HomeAssistant, entry: RoomClimateConfigEntry, room: Room
) -> dict:
    eid = entry.entry_id

    def rr(key: str, domain: str) -> str | None:
        return resolve_room_entity(hass, eid, room.key, key, domain)

    live = {
        device: {
            "use": rr(KEY_USE[device], "switch"),
            "target": rr(KEY_TARGET[device], "number"),
            "medium_offset": rr(KEY_MEDIUM_OFFSET[device], "number"),
            "high_offset": rr(KEY_HIGH_OFFSET[device], "number"),
        }
        for device in room.devices
        if device != DEVICE_FAN
    }
    fans = [
        {
            "entity_id": fan_eid,
            "slug": (slug := fan_slug(fan_eid)),
            "label": _fan_label(hass, fan_eid),
            # Detected live from the fan entity's DIRECTION capability (CC-22); the
            # card only shows the Reverse toggle / direction text when true.
            "reversible": fan_supports_direction(hass, fan_eid),
            "use": rr(fan_use_key(slug), "switch"),
            "target": rr(fan_target_key(slug), "number"),
            "reverse": rr(fan_reverse_key(slug), "switch"),
        }
        for fan_eid in room.fan_entities
    ]
    return {
        "key": room.key,
        "label": room.label,
        "area_id": room.area_id,
        "has_ac": room.has_ac,
        "has_heating": room.has_heater,
        "has_fan": room.has_fan,
        "combined": room.combined,
        "entities": {
            "manual_mode": rr(KEY_MANUAL_MODE, "switch"),
            "ac_fan_only_override": (
                rr(KEY_AC_FAN_ONLY, "switch")
                if room.has_ac and room.ac_fan_only
                else None
            ),
            "heater_fan_only_override": (
                rr(KEY_HEATER_FAN_ONLY, "switch")
                if room.has_heater and room.heater_fan_only
                else None
            ),
            "temperature": rr(KEY_ROOM_TEMPERATURE, "sensor"),
            "humidity": rr(KEY_ROOM_HUMIDITY, "sensor")
            if room.humidity_sensor
            else None,
            "power": rr(KEY_ROOM_POWER, "sensor") if room.power_sensor else None,
            # Hub-level outdoor temperature abstraction (same for every room).
            "outdoor": resolve_hub_entity(hass, eid, KEY_OUTDOOR_TEMPERATURE, "sensor"),
            # Hub-level graph time-range selector (same for every room).
            "time_range": resolve_hub_entity(hass, eid, KEY_GRAPH_TIME_RANGE, "select"),
            # The physical devices the room drives (so the card can render their
            # state without re-specifying them — they live in the subentry config).
            "ac_entity": room.ac_climate if room.has_ac else None,
            "heater_entity": room.heater_climate if room.has_heater else None,
            # Per-fan live entities (each fan has its own target/use/reverse); the
            # fan-speed offsets below are shared across all the room's fans.
            "fans": fans,
            "fan_offsets": (
                {
                    "medium_offset": rr(KEY_MEDIUM_OFFSET[DEVICE_FAN], "number"),
                    "high_offset": rr(KEY_HIGH_OFFSET[DEVICE_FAN], "number"),
                }
                if room.has_fan
                else None
            ),
            # Room-level humidity target/offsets driving the fans (CC-28); only
            # present when the room has both a humidity sensor and a fan.
            "humidity_control": (
                {
                    "target": rr(KEY_HUMIDITY_TARGET, "number"),
                    "medium_offset": rr(KEY_HUMIDITY_MEDIUM_OFFSET, "number"),
                    "high_offset": rr(KEY_HUMIDITY_HIGH_OFFSET, "number"),
                }
                if room.has_fan and room.humidity_sensor
                else None
            ),
            # Optional window contacts the card reads directly from hass.states to
            # disable cooling/heating Use toggles while any is open (UX-26); [] if
            # none configured.
            "window_sensors": list(room.window_sensors),
            "live": live,
        },
    }


def _serialize_profile(
    hass: HomeAssistant, entry: RoomClimateConfigEntry, profile: Profile
) -> dict:
    eid = entry.entry_id
    room = entry.runtime_data.room_by_key(profile.room)
    devices = room.devices if room else ()

    def rp(key: str, domain: str) -> str | None:
        return resolve_profile_entity(hass, eid, profile.id, key, domain)

    presets = {}
    for device in devices:
        if device == DEVICE_FAN:
            continue
        preset = profile.presets.get(device)
        presets[device] = {
            "use": preset.use if preset else False,
            "temp": preset.temp if preset else None,
            "use_entity": rp(KEY_PROFILE_USE[device], "switch"),
            "temp_entity": rp(KEY_PROFILE_PRESET[device], "number"),
        }
    fan_presets = []
    for fan_eid in room.fan_entities if room else ():
        slug = fan_slug(fan_eid)
        fp = profile.fan_presets.get(slug)
        fan_presets.append(
            {
                "slug": slug,
                "label": _fan_label(hass, fan_eid),
                "use": fp.use if fp else False,
                "temp": fp.temp if fp else None,
                "reverse": fp.reverse if fp else False,
                "reversible": fan_supports_direction(hass, fan_eid),
                "use_entity": rp(profile_fan_use_key(slug), "switch"),
                "temp_entity": rp(profile_fan_temp_key(slug), "number"),
                "reverse_entity": rp(profile_fan_reverse_key(slug), "switch"),
            }
        )
    has_fan_override = bool(room and room.has_ac and room.ac_fan_only)
    return {
        "id": profile.id,
        "name": profile.name,
        "room": profile.room,
        "icon": profile.icon,
        "enabled": profile.enabled,
        "time": profile.time,
        "has_heating": room.has_heater if room else False,
        "has_fan": room.has_fan if room else False,
        "fan_override": profile.fan_override if has_fan_override else None,
        "entities": {
            "enabled": rp(KEY_PROFILE_ENABLED, "switch"),
            "time": rp(KEY_PROFILE_TIME, "time"),
            "fan_override": (
                rp(KEY_PROFILE_FAN_OVERRIDE, "switch") if has_fan_override else None
            ),
            "presets": presets,
            "fan_presets": fan_presets,
        },
    }


# ---------------------------------------------------------------------------
# Read commands
# ---------------------------------------------------------------------------
@websocket_api.websocket_command(
    {vol.Required("type"): "btoddb_room_climate_controller/rooms/list"}
)
@callback
def ws_list_rooms(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List configured rooms with their live entity ids."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    connection.send_result(
        msg["id"],
        {
            "rooms": [
                _serialize_room(hass, entry, r)
                for r in entry.runtime_data.rooms.values()
            ]
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "btoddb_room_climate_controller/profiles/list"}
)
@callback
def ws_list_profiles(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List all profiles with their preset entity ids."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    connection.send_result(
        msg["id"],
        {
            "profiles": [
                _serialize_profile(hass, entry, p) for p in entry.runtime_data.profiles
            ]
        },
    )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
@websocket_api.websocket_command(
    {
        vol.Required("type"): "btoddb_room_climate_controller/profiles/create",
        vol.Required("name"): str,
        vol.Required("room"): str,
        vol.Optional("time"): str,
        vol.Optional("copy_room_settings", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_create_profile(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Create a profile, spawn its entities, and schedule it."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    hub = entry.runtime_data
    name = msg["name"].strip()
    if not name:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_INVALID_FORMAT, "Name required"
        )
        return
    room = hub.room_by_key(msg["room"])
    if room is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Unknown room"
        )
        return

    run_time: str | None = None
    if raw_time := msg.get("time"):
        try:
            run_time = normalize_time_hhmm(raw_time)
        except ValueError as err:
            connection.send_error(
                msg["id"], websocket_api.const.ERR_INVALID_FORMAT, str(err)
            )
            return
        if conflict := find_time_conflict(hub.profiles, room.key, run_time):
            connection.send_error(
                msg["id"],
                websocket_api.const.ERR_NOT_ALLOWED,
                f"Profile {conflict} already runs at {run_time} in {room.label}",
            )
            return

    profile = Profile.with_defaults(
        profile_id=next_profile_id(hub.profile_ids), name=name, room=room
    )
    profile.time = run_time
    if msg["copy_room_settings"]:
        _copy_room_into_profile(hass, entry, room, profile)

    hub.profiles.append(profile)
    await hub.async_save()
    _PROFILE_LOGGER.info(
        "[room=%s profile=%s] Profile created: '%s'",
        room.key,
        profile.name,
        profile.name,
    )
    async_dispatcher_send(hass, SIGNAL_ADD_PROFILE_ENTITIES, profile)
    if hub.scheduler:
        hub.scheduler.async_refresh()
    # Place the new profile's device in its room's area once its entities register.
    async_call_later(hass, 5, callback(lambda _now: async_assign_areas(hass, entry)))
    connection.send_result(
        msg["id"], {"profile": _serialize_profile(hass, entry, profile)}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "btoddb_room_climate_controller/profiles/delete",
        vol.Required("profile_id"): str,
    }
)
@websocket_api.async_response
async def ws_delete_profile(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete a profile and tear down its entities + device."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    hub = entry.runtime_data
    pid = format_profile_id(msg["profile_id"])
    profile = hub.get_profile(pid)
    if profile is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Unknown profile"
        )
        return

    hub.profiles = [p for p in hub.profiles if p.id != pid]
    await hub.async_save()
    _PROFILE_LOGGER.info(
        "[room=%s profile=%s] Profile deleted: '%s'",
        profile.room,
        profile.name,
        profile.name,
    )
    async_dispatcher_send(hass, SIGNAL_REMOVE_PROFILE, pid)
    _remove_profile_device(hass, entry, pid)
    if hub.scheduler:
        hub.scheduler.async_refresh()
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "btoddb_room_climate_controller/profiles/rename",
        vol.Required("profile_id"): str,
        vol.Required("name"): str,
    }
)
@websocket_api.async_response
async def ws_rename_profile(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Rename a profile (updates its device name → entity friendly names)."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    hub = entry.runtime_data
    pid = format_profile_id(msg["profile_id"])
    profile = hub.get_profile(pid)
    if profile is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Unknown profile"
        )
        return
    name = msg["name"].strip()
    if not name:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_INVALID_FORMAT, "Name required"
        )
        return

    old_name = profile.name
    profile.name = name
    await hub.async_save()
    _PROFILE_LOGGER.info(
        "[room=%s profile=%s] Profile renamed: '%s' → '%s'",
        profile.room,
        name,
        old_name,
        name,
    )
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_profile_{pid}")}
    )
    if device:
        dev_reg.async_update_device(device.id, name=name)
    connection.send_result(
        msg["id"], {"profile": _serialize_profile(hass, entry, profile)}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "btoddb_room_climate_controller/profiles/set_room",
        vol.Required("profile_id"): str,
        vol.Required("room"): str,
    }
)
@websocket_api.async_response
async def ws_set_room(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Move a profile to a different room (reloads to rebuild its entities)."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    hub = entry.runtime_data
    pid = format_profile_id(msg["profile_id"])
    profile = hub.get_profile(pid)
    if profile is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Unknown profile"
        )
        return
    room = hub.room_by_key(msg["room"])
    if room is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Unknown room"
        )
        return

    # The profile's preset entities depend on the room's device set, so rebuild
    # cleanly via reload rather than surgically swapping entities.
    hub.profiles = [p.reassigned_to(room) if p.id == pid else p for p in hub.profiles]
    await hub.async_save()
    await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "btoddb_room_climate_controller/profiles/apply",
        vol.Required("profile_id"): str,
    }
)
@websocket_api.async_response
async def ws_apply_profile(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Apply a profile's presets to its room now."""
    entry = _require_entry(hass, connection, msg)
    if entry is None:
        return
    profile = entry.runtime_data.get_profile(format_profile_id(msg["profile_id"]))
    if profile is None:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_NOT_FOUND, "Unknown profile"
        )
        return
    await async_apply_profile(entry, profile, force=True)
    connection.send_result(msg["id"], {"success": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _copy_room_into_profile(
    hass: HomeAssistant, entry: RoomClimateConfigEntry, room: Room, profile: Profile
) -> None:
    """Seed a new profile's presets from the room's current live values."""
    for device in room.devices:
        if device == DEVICE_FAN:
            continue
        preset = profile.presets.get(device)
        if preset is None:
            continue
        if use_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, KEY_USE[device], "switch"
        ):
            preset.use = hass.states.is_state(use_eid, STATE_ON)
        target_eid = resolve_room_entity(
            hass, entry.entry_id, room.key, KEY_TARGET[device], "number"
        )
        if target_eid and (state := hass.states.get(target_eid)) is not None:
            with contextlib.suppress(TypeError, ValueError):
                preset.temp = float(state.state)
    if (
        room.has_ac
        and room.ac_fan_only
        and (
            ov_eid := resolve_room_entity(
                hass, entry.entry_id, room.key, KEY_AC_FAN_ONLY, "switch"
            )
        )
    ):
        profile.fan_override = hass.states.is_state(ov_eid, STATE_ON)
    for fan_eid in room.fan_entities:
        slug = fan_slug(fan_eid)
        fp = profile.ensure_fan_preset(slug)
        if use_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, fan_use_key(slug), "switch"
        ):
            fp.use = hass.states.is_state(use_eid, STATE_ON)
        target_eid = resolve_room_entity(
            hass, entry.entry_id, room.key, fan_target_key(slug), "number"
        )
        if target_eid and (state := hass.states.get(target_eid)) is not None:
            with contextlib.suppress(TypeError, ValueError):
                fp.temp = float(state.state)
        if rev_eid := resolve_room_entity(
            hass, entry.entry_id, room.key, fan_reverse_key(slug), "switch"
        ):
            fp.reverse = hass.states.is_state(rev_eid, STATE_ON)


def _remove_profile_device(
    hass: HomeAssistant, entry: RoomClimateConfigEntry, profile_id: str
) -> None:
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_profile_{profile_id}")}
    )
    if device:
        dev_reg.async_remove_device(device.id)
