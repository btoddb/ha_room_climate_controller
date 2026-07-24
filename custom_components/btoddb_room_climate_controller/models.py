"""
Data models and shared helpers for Room Climate.

Pure logic (no Home Assistant imports) so it can be unit-tested and reused across
the config flow, websocket API, scheduler, and entity platforms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

from .const import (
    CONF_AC_CLIMATE,
    CONF_AC_FAN_ENTITY,
    CONF_AC_FAN_ONLY,
    CONF_AC_POWER_SWITCH,
    CONF_AREA_ID,
    CONF_COMBINED,
    CONF_COMMAND_DELAY,
    CONF_FAN_ENTITIES,
    CONF_FAN_ENTITY_LEGACY,
    CONF_HAS_AC,
    CONF_HAS_FAN,
    CONF_HAS_HEATER,
    CONF_HEATER_CLIMATE,
    CONF_HEATER_FAN_ENTITY,
    CONF_HEATER_FAN_ONLY,
    CONF_HEATER_POWER_SWITCH,
    CONF_HUMIDITY_SENSOR,
    CONF_LABEL,
    CONF_LIMITS,
    CONF_POWER_ON_DELAY,
    CONF_POWER_SENSOR,
    CONF_ROOM_KEY,
    CONF_TEMPERATURE_SENSOR,
    CONF_WINDOW_SENSORS,
    DEFAULT_COMMAND_DELAY,
    DEFAULT_LIMITS,
    DEFAULT_POWER_ON_DELAY,
    DEVICE_COOLING,
    DEVICE_FAN,
    DEVICE_HEATING,
    DEVICE_TYPES,
    PROFILE_DEFAULT_ICON,
)

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_MAX_HOUR = 23
_MAX_MINUTE = 59


# ---------------------------------------------------------------------------
# ID / time helpers (ported from daily_climate_lib.py)
# ---------------------------------------------------------------------------
def normalize_time_hhmm(value: str) -> str:
    """Validate and normalize a profile run time to HH:MM (24h)."""
    raw = str(value).strip()
    m = _TIME_RE.match(raw)
    if not m:
        msg = f"Invalid time {value!r}; use HH:MM (24-hour)"
        raise ValueError(msg)
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > _MAX_HOUR or minute > _MAX_MINUTE:
        msg = f"Invalid time {value!r}"
        raise ValueError(msg)
    return f"{hour:02d}:{minute:02d}"


def format_profile_id(profile_id: str | int) -> str:
    """Canonical id (e.g. 8 and 08 → 08); non-numeric ids pass through."""
    s = str(profile_id).strip()
    if s.isdigit():
        return f"{int(s):02d}"
    return s


def slugify_profile_id(name: str) -> str:
    """Derive a fallback profile id from a display name."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return base[:32] or "profile"


def fan_slug(entity_id: str) -> str:
    """Stable slug for a fan entity id, used in per-fan unique_id suffixes."""
    base = re.sub(r"[^a-z0-9]+", "_", str(entity_id).lower()).strip("_")
    return base or "fan"


# Per-fan ROOM live-entity key builders (unique_id suffixes under room_uid).
def fan_target_key(slug: str) -> str:
    """Room per-fan target-temp entity key suffix."""
    return f"target_fan_temp__{slug}"


def fan_use_key(slug: str) -> str:
    """Room per-fan Use-toggle entity key suffix."""
    return f"use_fan__{slug}"


def fan_reverse_key(slug: str) -> str:
    """Room per-fan Reverse-switch entity key suffix."""
    return f"fan_reverse__{slug}"


# Per-fan PROFILE preset-entity key builders (suffixes under profile_uid).
def profile_fan_temp_key(slug: str) -> str:
    """Profile per-fan preset target-temp entity key suffix."""
    return f"fan_{slug}"


def profile_fan_use_key(slug: str) -> str:
    """Profile per-fan preset Use-toggle entity key suffix."""
    return f"use_fan_{slug}"


def profile_fan_reverse_key(slug: str) -> str:
    """Profile per-fan preset Reverse-switch entity key suffix."""
    return f"fan_reverse_{slug}"


def next_profile_id(existing_ids: Iterable[str]) -> str:
    """Return the next 2-digit numeric id not already used."""
    ids = {format_profile_id(pid) for pid in existing_ids}
    numeric = [int(pid) for pid in ids if pid.isdigit()]
    candidate = max(numeric) + 1 if numeric else 1
    while f"{candidate:02d}" in ids:
        candidate += 1
    return f"{candidate:02d}"


def _coerce_window_sensors(data: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Normalize the stored window-sensor config into a tuple of entity ids.

    Accepts the current list form (``window_sensors``) and the earlier
    single-value ``window_sensor`` key (never shipped in a release, but may exist
    in dev/test storage), and tolerates a bare string for either.
    """
    raw = data.get(CONF_WINDOW_SENSORS)
    if raw is None:
        raw = data.get("window_sensor")  # legacy single-value key
    if not raw:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(eid for eid in raw if eid)


def _coerce_fan_entities(data: Mapping[str, Any]) -> tuple[str, ...]:
    raw = data.get(CONF_FAN_ENTITIES)
    if raw is None:
        raw = data.get(CONF_FAN_ENTITY_LEGACY)  # pre-#66 single-fan key
    if not raw:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(eid for eid in raw if eid)


# ---------------------------------------------------------------------------
# Room model (from a config subentry)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Room:
    """A configured room and its physical devices."""

    room_id: str  # subentry_id
    key: str
    label: str
    area_id: str | None
    has_ac: bool
    has_heater: bool
    has_fan: bool
    combined: bool
    ac_climate: str | None
    heater_climate: str | None
    fan_entities: tuple[str, ...]
    ac_fan_entity: str | None
    heater_fan_entity: str | None
    ac_power_switch: str | None
    heater_power_switch: str | None
    temperature_sensor: str | None
    humidity_sensor: str | None
    power_sensor: str | None
    window_sensors: tuple[str, ...]
    ac_fan_only: bool
    heater_fan_only: bool
    limits: dict[str, dict[str, float]]
    command_delay: float
    power_on_delay: float

    @classmethod
    def from_subentry(cls, subentry_id: str, data: Mapping[str, Any]) -> Room:
        """Build a Room from a config subentry's stored data."""
        limits = {
            device: {
                "min": float(
                    data.get(CONF_LIMITS, {})
                    .get(device, {})
                    .get("min", DEFAULT_LIMITS[device]["min"])
                ),
                "max": float(
                    data.get(CONF_LIMITS, {})
                    .get(device, {})
                    .get("max", DEFAULT_LIMITS[device]["max"])
                ),
            }
            for device in DEVICE_TYPES
        }
        return cls(
            room_id=subentry_id,
            key=str(data[CONF_ROOM_KEY]),
            label=str(data.get(CONF_LABEL) or data[CONF_ROOM_KEY]),
            area_id=data.get(CONF_AREA_ID) or None,
            has_ac=bool(data.get(CONF_HAS_AC, True)),
            has_heater=bool(data.get(CONF_HAS_HEATER, False)),
            has_fan=bool(data.get(CONF_HAS_FAN, False)),
            combined=bool(data.get(CONF_COMBINED, False)),
            ac_climate=data.get(CONF_AC_CLIMATE) or None,
            heater_climate=data.get(CONF_HEATER_CLIMATE) or None,
            fan_entities=_coerce_fan_entities(data),
            ac_fan_entity=data.get(CONF_AC_FAN_ENTITY) or None,
            heater_fan_entity=data.get(CONF_HEATER_FAN_ENTITY) or None,
            ac_power_switch=data.get(CONF_AC_POWER_SWITCH) or None,
            heater_power_switch=data.get(CONF_HEATER_POWER_SWITCH) or None,
            temperature_sensor=data.get(CONF_TEMPERATURE_SENSOR) or None,
            humidity_sensor=data.get(CONF_HUMIDITY_SENSOR) or None,
            power_sensor=data.get(CONF_POWER_SENSOR) or None,
            window_sensors=_coerce_window_sensors(data),
            ac_fan_only=bool(data.get(CONF_AC_FAN_ONLY, False)),
            heater_fan_only=bool(data.get(CONF_HEATER_FAN_ONLY, False)),
            limits=limits,
            command_delay=float(data.get(CONF_COMMAND_DELAY, DEFAULT_COMMAND_DELAY)),
            power_on_delay=float(data.get(CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY)),
        )

    def supports(self, device: str) -> bool:
        """Whether this room has the given device type (cooling/heating/fan)."""
        if device == DEVICE_COOLING:
            return self.has_ac
        if device == DEVICE_HEATING:
            return self.has_heater
        if device == DEVICE_FAN:
            return self.has_fan
        return False

    @property
    def devices(self) -> tuple[str, ...]:
        """Device types available in this room, in canonical order."""
        return tuple(d for d in DEVICE_TYPES if self.supports(d))


def describe_room_settings(room: Room) -> str:
    """
    Render a room's full configuration as one log-friendly string (CC-L5).

    Pure/HA-free so it can be unit-tested directly; used by ``hub.py`` to append
    the room's settings to "Room created" / "Room settings changed" log lines.
    """
    parts = [
        f"devices={list(room.devices)}",
        f"combined={room.combined}",
    ]
    if room.has_ac:
        parts.append(f"ac_climate={room.ac_climate}")
        if room.ac_fan_entity:
            parts.append(f"ac_fan_entity={room.ac_fan_entity}")
        if room.ac_power_switch:
            parts.append(f"ac_power_switch={room.ac_power_switch}")
        parts.append(f"ac_fan_only={room.ac_fan_only}")
    if room.has_heater:
        parts.append(f"heater_climate={room.heater_climate}")
        if room.heater_fan_entity:
            parts.append(f"heater_fan_entity={room.heater_fan_entity}")
        if room.heater_power_switch:
            parts.append(f"heater_power_switch={room.heater_power_switch}")
        parts.append(f"heater_fan_only={room.heater_fan_only}")
    if room.has_fan:
        parts.append(f"fan_entities={list(room.fan_entities)}")
    parts.append(f"temperature_sensor={room.temperature_sensor}")
    if room.humidity_sensor:
        parts.append(f"humidity_sensor={room.humidity_sensor}")
    if room.power_sensor:
        parts.append(f"power_sensor={room.power_sensor}")
    if room.window_sensors:
        parts.append(f"window_sensors={list(room.window_sensors)}")
    for device in room.devices:
        limits = room.limits[device]
        parts.append(f"{device}_limits=[{limits['min']:.0f},{limits['max']:.0f}]°F")
    parts.append(f"command_delay={room.command_delay}s")
    parts.append(f"power_on_delay={room.power_on_delay}s")
    if room.area_id:
        parts.append(f"area_id={room.area_id}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Profile model (stored in .storage, source of truth for preset values)
# ---------------------------------------------------------------------------
@dataclass
class DevicePreset:
    """A profile's per-device preset: whether to apply it and the target temp."""

    use: bool = False
    temp: float = 0.0


@dataclass
class FanPreset:
    """A profile's per-fan preset: whether to apply it, target temp, and reverse."""

    use: bool = False
    temp: float = 0.0
    reverse: bool = False


@dataclass
class Profile:
    """A scheduled climate preset for a room."""

    id: str
    name: str
    room: str  # room key
    icon: str = PROFILE_DEFAULT_ICON
    enabled: bool = True
    time: str | None = None  # "HH:MM"
    fan_override: bool = False
    fan_reverse: bool = False  # legacy single-fan field, read for migration only
    presets: dict[str, DevicePreset] = field(default_factory=dict)
    fan_presets: dict[str, FanPreset] = field(default_factory=dict)

    @classmethod
    def with_defaults(cls, *, profile_id: str, name: str, room: Room) -> Profile:
        """Create a profile with preset defaults (uses off, temps at room min)."""
        return cls(
            id=format_profile_id(profile_id),
            name=name.strip(),
            room=room.key,
            presets={
                device: DevicePreset(use=False, temp=room.limits[device]["min"])
                for device in room.devices
                if device != DEVICE_FAN
            },
            fan_presets={
                fan_slug(eid): FanPreset(use=False, temp=room.limits[DEVICE_FAN]["min"])
                for eid in room.fan_entities
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "id": self.id,
            "name": self.name,
            "room": self.room,
            "icon": self.icon,
            "enabled": self.enabled,
            "time": self.time,
            "fan_override": self.fan_override,
            "fan_reverse": self.fan_reverse,
            "presets": {
                device: {"use": p.use, "temp": p.temp}
                for device, p in self.presets.items()
            },
            "fan_presets": {
                slug: {"use": p.use, "temp": p.temp, "reverse": p.reverse}
                for slug, p in self.fan_presets.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Profile:
        """Deserialize from storage."""
        return cls(
            id=format_profile_id(data["id"]),
            name=str(data["name"]),
            room=str(data["room"]),
            icon=str(data.get("icon") or PROFILE_DEFAULT_ICON),
            enabled=bool(data.get("enabled", True)),
            time=data.get("time"),
            fan_override=bool(data.get("fan_override", False)),
            fan_reverse=bool(data.get("fan_reverse", False)),
            presets={
                device: DevicePreset(
                    use=bool(p.get("use", False)),
                    temp=float(p.get("temp", 0.0)),
                )
                for device, p in (data.get("presets") or {}).items()
            },
            fan_presets={
                slug: FanPreset(
                    use=bool(p.get("use", False)),
                    temp=float(p.get("temp", 0.0)),
                    reverse=bool(p.get("reverse", False)),
                )
                for slug, p in (data.get("fan_presets") or {}).items()
            },
        )

    def ensure_preset(self, device: str) -> DevicePreset:
        """
        Return this profile's preset for ``device``, creating a default one if missing.

        A profile predates a device added to its room later (e.g. a fan attached
        to a room after the profile already existed): the device's preset
        entities are created from the room's *current* device set, but
        ``presets`` was never backfilled, so edits to those entities were
        silently dropped instead of being persisted.
        """
        preset = self.presets.get(device)
        if preset is None:
            preset = DevicePreset()
            self.presets[device] = preset
        return preset

    def ensure_fan_preset(self, slug: str) -> FanPreset:
        """Return the fan preset for ``slug``, creating a default one if missing."""
        preset = self.fan_presets.get(slug)
        if preset is None:
            preset = FanPreset()
            self.fan_presets[slug] = preset
        return preset

    def migrate_fan_presets(self, room: Room) -> bool:
        """
        Convert a legacy single-fan profile to per-fan presets for ``room``'s fans.

        Old profiles stored a single ``presets["fan"]`` (+ profile-level
        ``fan_reverse``). Seed every fan of the room from that legacy value so the
        multi-fan profile starts where the single fan was, then drop the legacy
        ``presets["fan"]``. No-op once ``fan_presets`` is populated or the room has
        no fans. Returns True if anything changed (so the caller can persist).
        """
        if not room.has_fan or self.fan_presets:
            return False
        legacy = self.presets.get(DEVICE_FAN)
        for eid in room.fan_entities:
            self.fan_presets[fan_slug(eid)] = FanPreset(
                use=legacy.use if legacy else False,
                temp=legacy.temp if legacy else room.limits[DEVICE_FAN]["min"],
                reverse=self.fan_reverse,
            )
        changed = bool(room.fan_entities)
        self.presets.pop(DEVICE_FAN, None)
        return changed

    def reassigned_to(self, room: Room) -> Profile:
        """Return a copy moved to ``room``, re-seeding presets for its devices."""
        presets = {
            device: self.presets.get(
                device, DevicePreset(use=False, temp=room.limits[device]["min"])
            )
            for device in room.devices
            if device != DEVICE_FAN
        }
        fan_presets = {
            fan_slug(eid): self.fan_presets.get(
                fan_slug(eid), FanPreset(temp=room.limits[DEVICE_FAN]["min"])
            )
            for eid in room.fan_entities
        }
        return replace(self, room=room.key, presets=presets, fan_presets=fan_presets)


# ---------------------------------------------------------------------------
# Deterministic unique_id builders (used by platforms + controller/scheduler)
# ---------------------------------------------------------------------------
def room_uid(entry_id: str, room_key: str, key: str) -> str:
    """Build the unique id for a per-room entity."""
    return f"{entry_id}_room_{room_key}_{key}"


def profile_uid(entry_id: str, profile_id: str, key: str) -> str:
    """Build the unique id for a per-profile entity."""
    return f"{entry_id}_profile_{profile_id}_{key}"


def find_time_conflict(
    profiles: Iterable[Profile],
    room_key: str,
    new_time: str,
    *,
    exclude_id: str | None = None,
) -> str | None:
    """Return the id of a profile already scheduled at ``new_time`` in the room."""
    normalized = normalize_time_hhmm(new_time)
    exclude = format_profile_id(exclude_id) if exclude_id else None
    for profile in profiles:
        if profile.room != room_key:
            continue
        if exclude and profile.id == exclude:
            continue
        if profile.time and normalize_time_hhmm(profile.time) == normalized:
            return profile.id
    return None
