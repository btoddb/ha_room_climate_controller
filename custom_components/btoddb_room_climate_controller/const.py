"""Constants for the Room Climate integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "btoddb_room_climate_controller"

# --- Child loggers -----------------------------------------------------------
# Per-category loggers under the integration's base namespace so HA's logger
# config can set a level per category (and the category shows in the
# logger-name column), independent of the module that happens to emit it.
LOGGER_SENSOR: Final = f"custom_components.{DOMAIN}.sensor"
LOGGER_SETTINGS: Final = f"custom_components.{DOMAIN}.settings"
LOGGER_PROFILE: Final = f"custom_components.{DOMAIN}.profile"
LOGGER_CAPABILITIES: Final = f"custom_components.{DOMAIN}.capabilities"

PLATFORMS: Final = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
]

# --- Storage (profiles are runtime data, not config) ------------------------
STORAGE_KEY: Final = "room_climate_profiles"
STORAGE_VERSION: Final = 1

# --- Config subentries ------------------------------------------------------
# Each room is a config subentry of the single hub config entry.
SUBENTRY_TYPE_ROOM: Final = "room"

# --- Dispatcher signals -----------------------------------------------------
# Platforms subscribe to these to add/remove entities as rooms/profiles change.
SIGNAL_ADD_ROOM_ENTITIES: Final = f"{DOMAIN}_add_room_entities"
SIGNAL_ADD_PROFILE_ENTITIES: Final = f"{DOMAIN}_add_profile_entities"
SIGNAL_REMOVE_PROFILE: Final = f"{DOMAIN}_remove_profile"

# --- Device types (cooling / heating / fan / vent fan) -----------------------
DEVICE_COOLING: Final = "cooling"
DEVICE_HEATING: Final = "heating"
DEVICE_FAN: Final = "fan"
# CC-36: a single on/off switch|fan entity, triggered by temp OR humidity
# against its own targets — appended last so the generic per-device loops
# (Use switch, target number, profile preset) pick it up for free.
DEVICE_VENT: Final = "vent_fan"
DEVICE_TYPES: Final = (DEVICE_COOLING, DEVICE_HEATING, DEVICE_FAN, DEVICE_VENT)

DEVICE_ICONS: Final = {
    DEVICE_COOLING: "mdi:snowflake-thermometer",
    DEVICE_HEATING: "mdi:thermometer-high",
    DEVICE_FAN: "mdi:fan",
    DEVICE_VENT: "mdi:fan-clock",
}
DEVICE_USE_ICONS: Final = {
    DEVICE_COOLING: "mdi:snowflake",
    DEVICE_HEATING: "mdi:fire",
    DEVICE_FAN: "mdi:fan",
    DEVICE_VENT: "mdi:fan-clock",
}

# --- Room subentry config keys ----------------------------------------------
CONF_ROOM_KEY: Final = "room_key"
CONF_LABEL: Final = "label"
CONF_AREA_ID: Final = "area_id"

CONF_HAS_AC: Final = "has_ac"
CONF_HAS_HEATER: Final = "has_heater"
CONF_HAS_FAN: Final = "has_fan"
CONF_HAS_VENT_FAN: Final = "has_vent_fan"
CONF_COMBINED: Final = "combined"  # ac_climate == heater_climate (heat pump)

# Device entities driven by the reactive engine
CONF_AC_CLIMATE: Final = "ac_climate"
CONF_HEATER_CLIMATE: Final = "heater_climate"
CONF_FAN_ENTITIES: Final = "fan_entities"
CONF_FAN_ENTITY_LEGACY: Final = "fan_entity"  # pre-#66 single-fan key
CONF_VENT_FAN_ENTITY: Final = "vent_fan_entity"  # switch OR fan domain (CC-36)
CONF_AC_FAN_ENTITY: Final = "ac_fan_entity"
CONF_HEATER_FAN_ENTITY: Final = "heater_fan_entity"
CONF_AC_POWER_SWITCH: Final = "ac_power_switch"
CONF_HEATER_POWER_SWITCH: Final = "heater_power_switch"

# Source sensors mirrored into per-room sensor entities
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"
CONF_HUMIDITY_SENSOR: Final = "humidity_sensor"
CONF_POWER_SENSOR: Final = "power_sensor"

# Optional per-room window contacts (binary_sensors). When any reads "on" (open),
# the engine suppresses active cooling/heating; fan-only circulation is
# unaffected (CC-20). Stored as a list of entity ids.
CONF_WINDOW_SENSORS: Final = "window_sensors"

# Hub-level outdoor temperature source, mirrored into one abstraction sensor.
# Optional: if unset, no outdoor-temperature mirror is created.
CONF_OUTDOOR_SENSOR: Final = "outdoor_sensor"

# Hub-level graph time-range selector (hours the card's graphs show). Owned by
# the integration so a fresh install has a working selector without the user
# having to create an input_select helper.
GRAPH_TIME_RANGE_OPTIONS: Final = ["6", "12", "24", "48", "168"]
DEFAULT_GRAPH_TIME_RANGE: Final = "24"

# Fan-only override availability
CONF_AC_FAN_ONLY: Final = "ac_fan_only_override"
CONF_HEATER_FAN_ONLY: Final = "heater_fan_only_override"

# Per-device preset limits {min,max}
CONF_LIMITS: Final = "limits"

# Timing
CONF_COMMAND_DELAY: Final = "command_delay_seconds"
CONF_POWER_ON_DELAY: Final = "power_on_delay_seconds"

DEFAULT_COMMAND_DELAY: Final = 2
DEFAULT_POWER_ON_DELAY: Final = 3

# Default per-device temperature limits (°F), used to seed the config flow.
DEFAULT_LIMITS: Final = {
    DEVICE_COOLING: {"min": 60, "max": 86},
    DEVICE_HEATING: {"min": 45, "max": 95},
    DEVICE_FAN: {"min": 60, "max": 86},
    DEVICE_VENT: {"min": 60, "max": 86},
}

# Fan speed offset bounds (°F above/below target that bump speed low→med→high).
OFFSET_MIN: Final = 1
OFFSET_MAX: Final = 20
DEFAULT_MEDIUM_OFFSET: Final = 3
DEFAULT_HIGH_OFFSET: Final = 6

# CC-35: optional per-room device setpoint offsets. While conditioning, the
# device's own setpoint tracks the room target by this many °F (heater: +,
# A/C: -), keeping the device's internal thermostat from stopping early.
# Named distinctly from OFFSET_MIN/MAX (the fan-speed-tier offsets above).
CONF_HEATER_SETPOINT_OFFSET: Final = "heater_setpoint_offset"
CONF_AC_SETPOINT_OFFSET: Final = "ac_setpoint_offset"
DEFAULT_SETPOINT_OFFSET: Final = 2
SETPOINT_OFFSET_MIN: Final = 0
SETPOINT_OFFSET_MAX: Final = 20

TEMP_UNIT: Final = "°F"

# Humidity target + fan speed offset bounds (%RH above target that bump speed
# low→med→high). Only used when a room has both a humidity sensor and a fan.
HUMIDITY_UNIT: Final = "%"
HUMIDITY_TARGET_MIN: Final = 30
HUMIDITY_TARGET_MAX: Final = 90
DEFAULT_HUMIDITY_TARGET: Final = 60
HUMIDITY_OFFSET_MIN: Final = 1
HUMIDITY_OFFSET_MAX: Final = 30
DEFAULT_HUMIDITY_MEDIUM_OFFSET: Final = 5
DEFAULT_HUMIDITY_HIGH_OFFSET: Final = 10

# --- Room entity keys (unique_id / object_id suffixes) ----------------------
# Shared by entity creation and the controller's entity-registry resolution.
KEY_TARGET: Final = {
    DEVICE_COOLING: "target_cooling_temp",
    DEVICE_HEATING: "target_heating_temp",
    DEVICE_FAN: "target_fan_temp",
    DEVICE_VENT: "target_vent_fan_temp",
}
# Vent fan has no fan-speed offsets (on/off only, CC-36); these dummy entries
# exist only so the generic per-device loops that index KEY_MEDIUM_OFFSET /
# KEY_HIGH_OFFSET by device type never KeyError on DEVICE_VENT. No entity is
# ever created under them — resolving them always serializes null.
KEY_MEDIUM_OFFSET: Final = {
    DEVICE_COOLING: "cooling_medium_offset",
    DEVICE_HEATING: "heating_medium_offset",
    DEVICE_FAN: "fan_medium_offset",
    DEVICE_VENT: "vent_fan_medium_offset",
}
KEY_HIGH_OFFSET: Final = {
    DEVICE_COOLING: "cooling_high_offset",
    DEVICE_HEATING: "heating_high_offset",
    DEVICE_FAN: "fan_high_offset",
    DEVICE_VENT: "vent_fan_high_offset",
}
KEY_USE: Final = {
    DEVICE_COOLING: "use_ac",
    DEVICE_HEATING: "use_heater",
    DEVICE_FAN: "use_fan",
    DEVICE_VENT: "use_vent_fan",
}
# Legacy shared room-level humidity entities (CC-28, pre-#77). Kept only so
# __init__.py's one-time registry cleanup can find and remove them; no entity
# is created under them anymore — humidity is per-device (see
# fan_humidity_*_key / KEY_VENT_HUMIDITY_TARGET in models.py).
KEY_HUMIDITY_TARGET: Final = "humidity_target"
KEY_HUMIDITY_MEDIUM_OFFSET: Final = "humidity_medium_offset"
KEY_HUMIDITY_HIGH_OFFSET: Final = "humidity_high_offset"
# Vent fan's own humidity target (gated on the room having a humidity sensor).
KEY_VENT_HUMIDITY_TARGET: Final = "vent_fan_humidity_target"
KEY_MANUAL_MODE: Final = "manual_mode"
KEY_AC_FAN_ONLY: Final = "ac_fan_only_override"
KEY_HEATER_FAN_ONLY: Final = "heater_fan_only_override"
KEY_ROOM_TEMPERATURE: Final = "room_temperature"
KEY_ROOM_HUMIDITY: Final = "room_humidity"
KEY_ROOM_POWER: Final = "room_power"

# --- Hub entity keys --------------------------------------------------------
KEY_OUTDOOR_TEMPERATURE: Final = "outdoor_temperature"
KEY_GRAPH_TIME_RANGE: Final = "graph_time_range"

# --- Profile entity keys ----------------------------------------------------
KEY_PROFILE_ENABLED: Final = "enabled"
KEY_PROFILE_TIME: Final = "time"
KEY_PROFILE_FAN_OVERRIDE: Final = "fan_override"
KEY_PROFILE_PRESET: Final = {  # preset target temp per device
    DEVICE_COOLING: "cooling",
    DEVICE_HEATING: "heating",
    DEVICE_FAN: "fan",
    DEVICE_VENT: "vent_fan",
}
KEY_PROFILE_USE: Final = {  # preset use toggle per device
    DEVICE_COOLING: "use_cooling",
    DEVICE_HEATING: "use_heating",
    DEVICE_FAN: "use_fan",
    DEVICE_VENT: "use_vent_fan",
}
# Vent fan's profile humidity-target preset entity (PR-13).
KEY_PROFILE_VENT_HUMIDITY: Final = "vent_fan_humidity"

# --- Profile record keys ----------------------------------------------------
ATTR_PROFILE_ID: Final = "profile_id"
PROFILE_DEFAULT_ICON: Final = "mdi:calendar-clock"
