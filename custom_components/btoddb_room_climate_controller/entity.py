"""Base entities and device-info helpers for Room Climate."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.fan import FanEntityFeature
from homeassistant.core import HomeAssistant, callback

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_REMOVE_PROFILE
from .models import (
    Profile,
    Room,
    fan_reverse_key,
    fan_slug,
    fan_target_key,
    fan_use_key,
    profile_fan_reverse_key,
    profile_fan_temp_key,
    profile_fan_use_key,
    profile_uid,
    room_uid,
)

_LOGGER = logging.getLogger(__name__)


def fan_supports_direction(hass: HomeAssistant, entity_id: str | None) -> bool:
    """
    Whether a fan entity can spin in reverse (CC-22).

    Detected live from the entity's DIRECTION capability bit, or — for fans
    that express direction as a preset mode (e.g. Dreo) — from the presence
    of a "reverse" entry in their preset_modes list.  False when the entity
    is missing or its state isn't loaded yet.
    """
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    if state is None:
        return False
    supported = int(state.attributes.get("supported_features") or 0)
    if supported & FanEntityFeature.DIRECTION:
        return True
    return "reverse" in (state.attributes.get("preset_modes") or [])


def fan_direction_via_preset(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Return True when the fan uses preset_mode for direction (CC-22 preset path)."""
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    if state is None:
        return False
    supported = int(state.attributes.get("supported_features") or 0)
    if supported & FanEntityFeature.DIRECTION:
        return False
    return "reverse" in (state.attributes.get("preset_modes") or [])


def describe_climate_capabilities(hass: HomeAssistant, entity_id: str | None) -> str:
    """
    One-line capability summary for a climate entity (CC-L10).

    Shared by ``controller.py`` (logged when the controller starts) and
    ``config_flow.py`` (logged when the entity is selected in room setup) so the
    two DEBUG dumps never drift apart.
    """
    if not entity_id:
        return "none"
    state = hass.states.get(entity_id)
    if state is None:
        return f"{entity_id} (unavailable)"
    attrs = state.attributes
    hvac_modes = list(attrs.get("hvac_modes") or ())
    return (
        f"{entity_id}: hvac_modes={hvac_modes}, "
        f"fan_only={'yes' if 'fan_only' in hvac_modes else 'no'}, "
        f"fan_modes={list(attrs.get('fan_modes') or ())}, "
        f"min_temp={attrs.get('min_temp')}, max_temp={attrs.get('max_temp')}"
    )


def describe_fan_capabilities(hass: HomeAssistant, entity_id: str | None) -> str:
    """One-line capability summary for a fan entity (CC-L10). See above."""
    if not entity_id:
        return "none"
    state = hass.states.get(entity_id)
    if state is None:
        return f"{entity_id} (unavailable)"
    attrs = state.attributes
    return (
        f"{entity_id}: preset_modes={list(attrs.get('preset_modes') or ())}, "
        f"reversible={'yes' if fan_supports_direction(hass, entity_id) else 'no'}, "
        f"percentage_step={attrs.get('percentage_step')}"
    )


def resolve_room_entity(
    hass: HomeAssistant, entry_id: str, room_key: str, key: str, domain: str
) -> str | None:
    """Resolve a room live entity's id from its deterministic unique_id."""
    return er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, room_uid(entry_id, room_key, key)
    )


def resolve_profile_entity(
    hass: HomeAssistant, entry_id: str, profile_id: str, key: str, domain: str
) -> str | None:
    """Resolve a profile entity's id from its deterministic unique_id."""
    return er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, profile_uid(entry_id, profile_id, key)
    )


def resolve_hub_entity(
    hass: HomeAssistant, entry_id: str, key: str, domain: str
) -> str | None:
    """Resolve a hub-scoped entity id (e.g. the outdoor temperature mirror)."""
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry_id}_{key}")


def hub_identifier(entry_id: str) -> tuple[str, str]:
    """Return the identifier tuple for the hub service device."""
    return (DOMAIN, entry_id)


def room_device_info(entry: ConfigEntry, room: Room) -> DeviceInfo:
    """Device grouping all of a room's live entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_room_{room.key}")},
        name=room.label,
        manufacturer="BToddB Room Climate Controller",
        model="Room",
        via_device=hub_identifier(entry.entry_id),
    )


def profile_device_info(entry: ConfigEntry, profile: Profile) -> DeviceInfo:
    """Device grouping all of a profile's preset entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_profile_{profile.id}")},
        name=profile.name,
        manufacturer="BToddB Room Climate Controller",
        model="Daily profile",
        via_device=hub_identifier(entry.entry_id),
    )


@callback
def async_assign_areas(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Place each room's device — and its profiles' devices — in the room's area.

    Only fills a device with no area yet, so a manual move in the UI is preserved.
    Devices register a moment after platform setup, so callers also schedule a
    delayed pass.
    """
    hub = getattr(entry, "runtime_data", None)
    if hub is None:
        return
    dev_reg = dr.async_get(hass)

    def _assign(suffix: str, area_id: str | None) -> None:
        if not area_id:
            return
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_{suffix}")}
        )
        if device is not None and device.area_id is None:
            dev_reg.async_update_device(device.id, area_id=area_id)

    for room in hub.rooms.values():
        _assign(f"room_{room.key}", room.area_id)
    for profile in hub.profiles:
        room = hub.room_by_key(profile.room)
        if room is not None:
            _assign(f"profile_{profile.id}", room.area_id)


@callback
def async_migrate_profile_subentries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Drop the legacy "no subentry" link from profile devices.

    Profiles used to register without a config subentry; they now belong to their
    room's subentry. Devices created before that change keep both links and so
    appear twice in the UI — remove the stale ``None`` membership once the room
    link is present. Devices register a moment after setup, so callers also
    schedule a delayed pass.
    """
    hub = getattr(entry, "runtime_data", None)
    if hub is None:
        return
    dev_reg = dr.async_get(hass)
    for profile in hub.profiles:
        room = hub.room_by_key(profile.room)
        if room is None:
            continue
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_profile_{profile.id}")}
        )
        if device is None:
            continue
        subentries = device.config_entries_subentries.get(entry.entry_id, set())
        if None in subentries and room.room_id in subentries:
            dev_reg.async_update_device(
                device.id,
                remove_config_entry_id=entry.entry_id,
                remove_config_subentry_id=None,
            )


@callback
def async_migrate_fan_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Rename pre-#66 single-fan unique_ids to the per-fan slugged form.

    Only for rooms with exactly one fan (unambiguous). Renames the room's
    target/use/reverse entities and each profile's fan preset entities so
    restored state and history survive the multi-fan upgrade. No-op when the
    slugged entity already exists or the legacy one doesn't.
    """
    hub = getattr(entry, "runtime_data", None)
    if hub is None:
        return
    ent_reg = er.async_get(hass)

    def _rename(domain: str, old_uid: str, new_uid: str) -> None:
        if old_uid == new_uid:
            return
        entity_id = ent_reg.async_get_entity_id(domain, DOMAIN, old_uid)
        if entity_id is None:
            return
        if ent_reg.async_get_entity_id(domain, DOMAIN, new_uid) is not None:
            return
        try:
            ent_reg.async_update_entity(entity_id, new_unique_id=new_uid)
        except ValueError:
            _LOGGER.debug(
                "Fan unique_id migration skipped for %s (%s -> %s)",
                entity_id,
                old_uid,
                new_uid,
            )

    rid = entry.entry_id
    for room in hub.rooms.values():
        if len(room.fan_entities) != 1:
            continue
        slug = fan_slug(room.fan_entities[0])
        rk = room.key
        # Legacy single-fan room suffixes: "target_fan_temp", "use_fan",
        # "fan_reverse".
        _rename(
            "number",
            room_uid(rid, rk, "target_fan_temp"),
            room_uid(rid, rk, fan_target_key(slug)),
        )
        _rename(
            "switch",
            room_uid(rid, rk, "use_fan"),
            room_uid(rid, rk, fan_use_key(slug)),
        )
        _rename(
            "switch",
            room_uid(rid, rk, "fan_reverse"),
            room_uid(rid, rk, fan_reverse_key(slug)),
        )
        for profile in hub.profiles:
            if profile.room != room.key:
                continue
            # Legacy profile fan-preset suffixes: "fan" (KEY_PROFILE_PRESET),
            # "use_fan" (KEY_PROFILE_USE), "fan_reverse".
            _rename(
                "number",
                profile_uid(rid, profile.id, "fan"),
                profile_uid(rid, profile.id, profile_fan_temp_key(slug)),
            )
            _rename(
                "switch",
                profile_uid(rid, profile.id, "use_fan"),
                profile_uid(rid, profile.id, profile_fan_use_key(slug)),
            )
            _rename(
                "switch",
                profile_uid(rid, profile.id, "fan_reverse"),
                profile_uid(rid, profile.id, profile_fan_reverse_key(slug)),
            )


class ProfileRemovalMixin:
    """
    Removes a profile entity when its profile is deleted via dispatcher.

    Classes set ``self._profile_id`` and call ``_connect_profile_removal()``
    from their own ``async_added_to_hass``.
    """

    _profile_id: str

    def _connect_profile_removal(self) -> None:
        self.async_on_remove(  # type: ignore[attr-defined]
            async_dispatcher_connect(
                self.hass,  # type: ignore[attr-defined]
                SIGNAL_REMOVE_PROFILE,
                self._on_remove_signal,
            )
        )

    @callback
    def _on_remove_signal(self, profile_id: str) -> None:
        if profile_id == self._profile_id:
            self.hass.async_create_task(  # type: ignore[attr-defined]
                self.async_remove(force_remove=True)  # type: ignore[attr-defined]
            )
