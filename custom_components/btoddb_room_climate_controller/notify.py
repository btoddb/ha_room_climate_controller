"""
Hold-ended notifications via the companion ``btoddb_notifications`` integration.

Sent when a thermostat setpoint hold (CC-41), a vent fan override (CC-38) or a
standalone fan override (CC-46) times out and the controller resumes driving
the device (CC-44). Best-effort: a missing service is warned about once per
process and skipped, and a failing call is logged — neither ever raises into
the controller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import NOTIFY_DOMAIN, NOTIFY_SERVICE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import Room

_LOGGER = logging.getLogger(__name__)

# Services already warned about as missing, so the warning is logged once per
# process rather than on every hold end.
_WARNED: set[str] = set()


async def async_send_hold_ended(
    hass: HomeAssistant, room: Room, device: str, message: str
) -> None:
    """
    Notify that a hold on ``device`` ("ac"/"heater"/"vent_fan"/"fan_<slug>") ended.

    Never raises (CC-44). The ``tag`` is stable per room and device so a newer
    notification replaces an older one for the same device.
    """
    if not hass.services.has_service(NOTIFY_DOMAIN, NOTIFY_SERVICE):
        service = f"{NOTIFY_DOMAIN}.{NOTIFY_SERVICE}"
        if service not in _WARNED:
            _WARNED.add(service)
            _LOGGER.warning(
                "[room=%s] %s.%s is not registered; "
                "hold-ended notifications are skipped",
                room.key,
                NOTIFY_DOMAIN,
                NOTIFY_SERVICE,
            )
        return
    try:
        await hass.services.async_call(
            NOTIFY_DOMAIN,
            NOTIFY_SERVICE,
            {
                "message": message,
                "title": f"Room Climate · {room.label}",
                "tag": f"rcc-hold-{room.key}-{device}",
            },
            blocking=True,
        )
    except Exception:
        _LOGGER.exception("[room=%s] hold-ended notification failed", room.key)
