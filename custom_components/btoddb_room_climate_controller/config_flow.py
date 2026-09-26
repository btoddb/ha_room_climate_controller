"""Config flow for Room Climate: a single hub plus one subentry per room."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_AC_CLIMATE,
    CONF_AC_FAN_ENTITY,
    CONF_AC_FAN_ONLY,
    CONF_AC_POWER_SWITCH,
    CONF_AREA_ID,
    CONF_COMBINED,
    CONF_COMMAND_DELAY,
    CONF_FAN_ENTITIES,
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
    CONF_OUTDOOR_SENSOR,
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
    DOMAIN,
    LOGGER_CAPABILITIES,
    SUBENTRY_TYPE_ROOM,
)
from .entity import describe_climate_capabilities, describe_fan_capabilities

_LOGGER = logging.getLogger(__name__)
_CAPABILITIES_LOGGER = logging.getLogger(LOGGER_CAPABILITIES)


class RoomClimateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the single hub config entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single Room Climate hub entry (with the outdoor sensor)."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(
                title="BToddB Room Climate Controller",
                data={CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR)},
            )
        schema = vol.Schema(
            {
                vol.Optional(CONF_OUTDOOR_SENSOR): _entity(
                    "sensor", device_classes=["temperature"]
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, _config_entry: Any
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Rooms are added as subentries of the hub."""
        return {SUBENTRY_TYPE_ROOM: RoomSubentryFlowHandler}


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
def _entity(
    domain: str,
    *,
    device_classes: list[str] | None = None,
    multiple: bool = False,
) -> Any:
    config = selector.EntitySelectorConfig(domain=domain)
    if device_classes:
        config["device_class"] = device_classes
    if multiple:
        config["multiple"] = True
    return selector.EntitySelector(config)


_CLIMATE = _entity("climate")
_FAN = _entity("fan")
_FAN_MULTI = _entity("fan", multiple=True)
_SWITCH = _entity("switch")
_BOOL = selector.BooleanSelector()


def _temp_number() -> Any:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=30, max=110, step=1, mode="box")
    )


def _delay_number() -> Any:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=30, step=1, mode="box")
    )


class RoomSubentryFlowHandler(ConfigSubentryFlow):
    """Multi-step flow to add or reconfigure one room."""

    def __init__(self) -> None:
        """Initialize accumulated data."""
        self._data: dict[str, Any] = {}

    # -- entry points --------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1: name and capabilities (add flow)."""
        return await self._async_step_basics(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1: name and capabilities (reconfigure flow)."""
        if not self._data:
            self._data = dict(self._get_reconfigure_subentry().data)
        return await self._async_step_basics(user_input)

    # -- step 1: basics + capabilities --------------------------------------
    async def _async_step_basics(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            label = str(user_input[CONF_LABEL]).strip()
            key = slugify(user_input.get(CONF_ROOM_KEY) or label)
            if not key:
                errors[CONF_ROOM_KEY] = "invalid_key"
            elif self._key_taken(key):
                errors[CONF_ROOM_KEY] = "key_exists"
            if not errors:
                self._data.update(
                    {
                        CONF_LABEL: label,
                        CONF_ROOM_KEY: key,
                        CONF_AREA_ID: user_input.get(CONF_AREA_ID),
                        CONF_HAS_AC: user_input[CONF_HAS_AC],
                        CONF_HAS_HEATER: user_input[CONF_HAS_HEATER],
                        CONF_HAS_FAN: user_input[CONF_HAS_FAN],
                        CONF_COMBINED: user_input[CONF_COMBINED],
                    }
                )
                _LOGGER.debug(
                    "[room=%s] basics: label=%r has_ac=%s has_heater=%s has_fan=%s",
                    key,
                    label,
                    user_input[CONF_HAS_AC],
                    user_input[CONF_HAS_HEATER],
                    user_input[CONF_HAS_FAN],
                )
                return await self.async_step_devices()

        schema = vol.Schema(
            {
                vol.Required(CONF_LABEL): selector.TextSelector(),
                vol.Optional(CONF_ROOM_KEY): selector.TextSelector(),
                vol.Optional(CONF_AREA_ID): selector.AreaSelector(),
                vol.Required(CONF_HAS_AC, default=True): _BOOL,
                vol.Required(CONF_HAS_HEATER, default=False): _BOOL,
                vol.Required(CONF_HAS_FAN, default=False): _BOOL,
                vol.Required(CONF_COMBINED, default=False): _BOOL,
            }
        )
        return self.async_show_form(
            step_id="reconfigure" if self._is_reconfigure else "user",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )

    # -- step 2: device entities --------------------------------------------
    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2: assign device entities (climate, fan, power switch)."""
        if user_input is not None:
            _LOGGER.debug(
                "[room=%s] devices submitted: %s",
                self._data.get(CONF_ROOM_KEY),
                user_input,
            )
            for vol_key, _ in self._device_fields():
                key = vol_key.schema
                self._data[key] = user_input.get(key)
            _LOGGER.debug(
                "[room=%s] devices saved: ac=%s ac_fan=%s heater=%s fans=%s",
                self._data.get(CONF_ROOM_KEY),
                self._data.get(CONF_AC_CLIMATE),
                self._data.get(CONF_AC_FAN_ENTITY),
                self._data.get(CONF_HEATER_CLIMATE),
                self._data.get(CONF_FAN_ENTITIES),
            )
            self._log_device_capabilities()
            return await self.async_step_sensors()

        combined = self._data.get(CONF_COMBINED)
        return self.async_show_form(
            step_id="devices",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(dict(self._device_fields())), self._data
            ),
            description_placeholders={"combined": "yes" if combined else "no"},
        )

    def _log_device_capabilities(self) -> None:
        """
        Dump the selected climate/fan entities' capabilities at INFO (CC-L10).

        Answers "does this A/C have a fan_only mode?" / "what fan modes does it
        support?" directly from room setup, sharing the same formatting helpers
        the controller uses once the room is running.
        """
        room_key = self._data.get(CONF_ROOM_KEY)
        for label, key in (
            ("A/C", CONF_AC_CLIMATE),
            ("Heater", CONF_HEATER_CLIMATE),
        ):
            if entity_id := self._data.get(key):
                _CAPABILITIES_LOGGER.info(
                    "[room=%s] %s capabilities: %s",
                    room_key,
                    label,
                    describe_climate_capabilities(self.hass, entity_id),
                )
        for entity_id in self._data.get(CONF_FAN_ENTITIES) or ():
            _CAPABILITIES_LOGGER.info(
                "[room=%s] Fan capabilities: %s",
                room_key,
                describe_fan_capabilities(self.hass, entity_id),
            )
        for label, key in (
            ("A/C fan", CONF_AC_FAN_ENTITY),
            ("Heater fan", CONF_HEATER_FAN_ENTITY),
        ):
            if entity_id := self._data.get(key):
                _CAPABILITIES_LOGGER.info(
                    "[room=%s] %s capabilities: %s",
                    room_key,
                    label,
                    describe_fan_capabilities(self.hass, entity_id),
                )

    # -- step 3: sensors, limits, timing ------------------------------------
    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 3: pick sensors, temperature limits, and timing delays."""
        if user_input is not None:
            _LOGGER.debug(
                "[room=%s] sensors submitted: temp=%s humidity=%s power=%s windows=%s",
                self._data.get(CONF_ROOM_KEY),
                user_input.get(CONF_TEMPERATURE_SENSOR),
                user_input.get(CONF_HUMIDITY_SENSOR),
                user_input.get(CONF_POWER_SENSOR),
                user_input.get(CONF_WINDOW_SENSORS),
            )
            limits: dict[str, dict[str, float]] = {}
            for device, lo, hi in (
                (DEVICE_COOLING, "cooling_min", "cooling_max"),
                (DEVICE_HEATING, "heating_min", "heating_max"),
                (DEVICE_FAN, "fan_min", "fan_max"),
            ):
                if lo in user_input and hi in user_input:
                    limits[device] = {
                        "min": float(user_input[lo]),
                        "max": float(user_input[hi]),
                    }
            self._data[CONF_LIMITS] = limits
            self._data[CONF_TEMPERATURE_SENSOR] = user_input[CONF_TEMPERATURE_SENSOR]
            self._data[CONF_HUMIDITY_SENSOR] = user_input.get(CONF_HUMIDITY_SENSOR)
            self._data[CONF_POWER_SENSOR] = user_input.get(CONF_POWER_SENSOR)
            # .get() (not user_input[...]) so clearing it on reconfigure removes it.
            self._data[CONF_WINDOW_SENSORS] = user_input.get(CONF_WINDOW_SENSORS)
            self._data[CONF_COMMAND_DELAY] = user_input[CONF_COMMAND_DELAY]
            self._data[CONF_POWER_ON_DELAY] = user_input[CONF_POWER_ON_DELAY]
            # Combined heat pump uses one climate entity for both modes.
            if self._data.get(CONF_COMBINED):
                self._data[CONF_HEATER_CLIMATE] = self._data.get(CONF_AC_CLIMATE)
            return self._finish()

        fields: dict[Any, Any] = {
            vol.Required(CONF_TEMPERATURE_SENSOR): _entity(
                "sensor", device_classes=["temperature"]
            ),
            vol.Optional(CONF_HUMIDITY_SENSOR): _entity(
                "sensor", device_classes=["humidity"]
            ),
            vol.Optional(CONF_POWER_SENSOR): _entity(
                "sensor", device_classes=["power"]
            ),
            vol.Optional(CONF_WINDOW_SENSORS): _entity(
                "binary_sensor",
                device_classes=["window", "door", "opening"],
                multiple=True,
            ),
        }
        for device, lo, hi in (
            (DEVICE_COOLING, "cooling_min", "cooling_max"),
            (DEVICE_HEATING, "heating_min", "heating_max"),
            (DEVICE_FAN, "fan_min", "fan_max"),
        ):
            if not self._device_enabled(device):
                continue
            fields[vol.Required(lo, default=DEFAULT_LIMITS[device]["min"])] = (
                _temp_number()
            )
            fields[vol.Required(hi, default=DEFAULT_LIMITS[device]["max"])] = (
                _temp_number()
            )
        fields[vol.Required(CONF_COMMAND_DELAY, default=DEFAULT_COMMAND_DELAY)] = (
            _delay_number()
        )
        fields[vol.Required(CONF_POWER_ON_DELAY, default=DEFAULT_POWER_ON_DELAY)] = (
            _delay_number()
        )

        return self.async_show_form(
            step_id="sensors",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), self._suggested_sensor_values()
            ),
        )

    # -- helpers -------------------------------------------------------------
    @property
    def _is_reconfigure(self) -> bool:
        return self.source == "reconfigure"

    def _device_enabled(self, device: str) -> bool:
        return {
            DEVICE_COOLING: self._data.get(CONF_HAS_AC),
            DEVICE_HEATING: self._data.get(CONF_HAS_HEATER),
            DEVICE_FAN: self._data.get(CONF_HAS_FAN),
        }.get(device, False)

    def _key_taken(self, key: str) -> bool:
        """Return True if another room subentry already uses this key."""
        current_id = (
            self._get_reconfigure_subentry().subentry_id
            if self._is_reconfigure
            else None
        )
        for subentry_id, subentry in self._get_entry().subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
                continue
            if subentry_id == current_id:
                continue
            if subentry.data.get(CONF_ROOM_KEY) == key:
                return True
        return False

    def _suggested_sensor_values(self) -> dict[str, Any]:
        """Flatten stored limits back into the per-field suggested values."""
        suggested = dict(self._data)
        limits = self._data.get(CONF_LIMITS) or {}
        for device, lo, hi in (
            (DEVICE_COOLING, "cooling_min", "cooling_max"),
            (DEVICE_HEATING, "heating_min", "heating_max"),
            (DEVICE_FAN, "fan_min", "fan_max"),
        ):
            if device in limits:
                suggested[lo] = limits[device].get("min")
                suggested[hi] = limits[device].get("max")
        return suggested

    def _device_fields(self) -> list[tuple[Any, Any]]:
        """
        Return devices-step schema fields gated by room capabilities.

        Both the form schema and the save loop iterate this so they can't drift.
        """
        fields: list[tuple[Any, Any]] = []
        if self._data.get(CONF_HAS_AC):
            fields += [
                (vol.Optional(CONF_AC_CLIMATE), _CLIMATE),
                (vol.Optional(CONF_AC_FAN_ENTITY), _FAN),
                (vol.Optional(CONF_AC_POWER_SWITCH), _SWITCH),
                (vol.Required(CONF_AC_FAN_ONLY, default=False), _BOOL),
            ]
        if self._data.get(CONF_HAS_HEATER) and not self._data.get(CONF_COMBINED):
            fields += [
                (vol.Optional(CONF_HEATER_CLIMATE), _CLIMATE),
                (vol.Optional(CONF_HEATER_FAN_ENTITY), _FAN),
                (vol.Optional(CONF_HEATER_POWER_SWITCH), _SWITCH),
                (vol.Required(CONF_HEATER_FAN_ONLY, default=False), _BOOL),
            ]
        if self._data.get(CONF_HAS_FAN):
            fields += [
                (vol.Optional(CONF_FAN_ENTITIES), _FAN_MULTI),
            ]
        return fields

    def _finish(self) -> SubentryFlowResult:
        """Create or update the subentry with the accumulated data."""
        title = self._data[CONF_LABEL]
        if self._is_reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                data=self._data,
            )
        return self.async_create_entry(title=title, data=self._data)
