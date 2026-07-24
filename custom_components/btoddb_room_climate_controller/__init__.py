"""The Room Climate integration."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later

if TYPE_CHECKING:
    from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .apply import async_apply_profile
from .const import DOMAIN, KEY_MANUAL_MODE, PLATFORMS
from .constraints import ConstraintsValidator
from .controller import RoomController
from .entity import (
    async_assign_areas,
    async_migrate_fan_entity_ids,
    async_migrate_profile_subentries,
    resolve_room_entity,
)
from .hub import RoomClimateConfigEntry, RoomClimateHub
from .models import format_profile_id
from .scheduler import ProfileScheduler

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_APPLY_PROFILE = "apply_profile"
SERVICE_SET_MANUAL_MODE = "set_manual_mode"

CARD_URL_BASE = "/btoddb_room_climate_controller"
CARD_FILENAME = "room-climate-control-card.js"


def _card_digest(path: Path) -> str:
    """Short content hash of the card bundle, used as a cache-busting query param."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


async def _async_register_card(hass: HomeAssistant) -> None:
    """
    Make the card available to dashboards without manual resource setup.

    The card must be a Lovelace *resource*, not an extra frontend module
    (add_extra_js_url): extra modules are baked into index.html at render time,
    and HA's service worker caches dashboard pages stale-while-revalidate. A
    page rendered while HA was still starting (this integration not yet set up)
    lacks the module import, and clients keep getting that cached copy — cards
    intermittently fail with "custom element doesn't exist: room-climate-control"
    until the cache turns over. Resources are fetched over websocket at
    dashboard load, so they can't go stale with the page. The content-hash
    query param busts HTTP/service-worker caches whenever the bundle changes.
    """
    card_path = Path(__file__).parent / "www" / CARD_FILENAME
    try:
        digest = await hass.async_add_executor_job(_card_digest, card_path)
    except OSError:
        _LOGGER.exception("Card bundle missing or unreadable: %s", card_path)
        return
    base_url = f"{CARD_URL_BASE}/{CARD_FILENAME}"
    url = f"{base_url}?v={digest}"

    resources = hass.data[LOVELACE_DATA].resources
    if not isinstance(resources, ResourceStorageCollection):
        # Resources are YAML-managed (read-only to us); fall back to the
        # index-injected module and accept the stale-index race.
        add_extra_js_url(hass, url)
        return

    await resources.async_get_info()  # force-load the collection from storage
    for item in resources.async_items():
        if item["url"].partition("?")[0] == base_url:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"url": url})
            return
    await resources.async_create_item({"res_type": "module", "url": url})


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register websocket commands and services (once)."""
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL_BASE,
                str(Path(__file__).parent / "www"),
                cache_headers=False,
            )
        ]
    )
    await _async_register_card(hass)
    websocket_api.async_setup(hass)

    def _loaded_entry() -> RoomClimateConfigEntry | None:
        return next(
            (
                entry
                for entry in hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED
            ),
            None,
        )

    async def _apply_profile(call: ServiceCall) -> None:
        entry = _loaded_entry()
        if entry is None:
            return
        profile = entry.runtime_data.get_profile(
            format_profile_id(call.data["profile_id"])
        )
        if profile is not None:
            await async_apply_profile(entry, profile, force=True)

    async def _set_manual_mode(call: ServiceCall) -> None:
        entry = _loaded_entry()
        if entry is None:
            return
        room = entry.runtime_data.room_by_key(call.data["room"])
        if room is None:
            return
        manual = resolve_room_entity(
            hass, entry.entry_id, room.key, KEY_MANUAL_MODE, "switch"
        )
        if manual:
            await hass.services.async_call(
                "switch",
                "turn_on" if call.data["enabled"] else "turn_off",
                {"entity_id": manual},
                blocking=True,
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PROFILE,
        _apply_profile,
        schema=vol.Schema({vol.Required("profile_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MANUAL_MODE,
        _set_manual_mode,
        schema=vol.Schema(
            {vol.Required("room"): cv.string, vol.Required("enabled"): cv.boolean}
        ),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> bool:
    """Set up Room Climate from a config entry."""
    hub = RoomClimateHub(hass, entry)
    await hub.async_load()
    entry.runtime_data = hub

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="BToddB Room Climate Controller",
        name=entry.title,
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Rename pre-#66 single-fan unique_ids to the per-fan slugged form before the
    # platforms add their per-fan entities (otherwise the new entities claim the
    # slugged unique_ids first and the rename becomes a no-op).
    async_migrate_fan_entity_ids(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start a reactive controller + constraints validator per room.
    for room in hub.rooms.values():
        controller = RoomController(hass, entry, room)
        validator = ConstraintsValidator(hass, entry, room)
        hub.controllers[room.key] = controller
        hub.validators[room.key] = validator
        controller.async_start()
        validator.async_start()

    # Start the profile scheduler.
    hub.scheduler = ProfileScheduler(hass, entry)
    hub.scheduler.async_start()

    # Drop each room/profile device into its room's area, and move legacy profile
    # devices under their room's subentry. Devices register a moment after the
    # platforms set up, so re-run once on a short delay too. Must be a @callback
    # so the delayed pass runs on the event loop (loop-only registry writes).
    @callback
    def _device_housekeeping(_now: Any = None) -> None:
        async_assign_areas(hass, entry)
        async_migrate_profile_subentries(hass, entry)

    _device_housekeeping()
    entry.async_on_unload(async_call_later(hass, 5, _device_housekeeping))

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RoomClimateConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: RoomClimateConfigEntry
) -> None:
    """Reload when subentries (rooms) change."""
    await hass.config_entries.async_reload(entry.entry_id)
