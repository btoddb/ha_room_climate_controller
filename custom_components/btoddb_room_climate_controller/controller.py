"""Per-room reactive controller: gathers state, runs the engine, drives devices."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    DEVICE_FAN,
    DOMAIN,
    KEY_AC_FAN_ONLY,
    KEY_HEATER_FAN_ONLY,
    KEY_HIGH_OFFSET,
    KEY_MANUAL_MODE,
    KEY_MEDIUM_OFFSET,
    KEY_TARGET,
    KEY_USE,
    LOGGER_CAPABILITIES,
    LOGGER_SENSOR,
)
from .engine import (
    ClimateInfo,
    Command,
    Delay,
    EngineInputs,
    FanControl,
    FanInfo,
    FanSetDirection,
    FanSetPercentage,
    FanSetPreset,
    FanTurnOff,
    FanTurnOn,
    SetFanMode,
    SetHvacMode,
    SetTemperature,
    SwitchInfo,
    SwitchTurnOff,
    SwitchTurnOn,
    TurnOffClimate,
    any_window_open,
    clamp_setpoint,
    compute_commands,
)
from .entity import (
    describe_climate_capabilities,
    describe_fan_capabilities,
    fan_direction_via_preset,
    fan_supports_direction,
)
from .models import (
    Room,
    fan_reverse_key,
    fan_slug,
    fan_target_key,
    fan_use_key,
    room_uid,
)

if TYPE_CHECKING:
    from .hub import RoomClimateConfigEntry

_LOGGER = logging.getLogger(__name__)
_SENSOR_LOGGER = logging.getLogger(LOGGER_SENSOR)
_CAPABILITIES_LOGGER = logging.getLogger(LOGGER_CAPABILITIES)

_INVALID = (None, "", STATE_UNKNOWN, STATE_UNAVAILABLE, "none")


def _service_for(cmd: Command) -> tuple[str, str, dict]:  # noqa: PLR0911
    """Map a command to a (domain, service, data) service call."""
    if isinstance(cmd, SetHvacMode):
        return (
            "climate",
            "set_hvac_mode",
            {
                "entity_id": cmd.entity_id,
                "hvac_mode": cmd.hvac_mode,
            },
        )
    if isinstance(cmd, TurnOffClimate):
        return "climate", "turn_off", {"entity_id": cmd.entity_id}
    if isinstance(cmd, SetTemperature):
        return (
            "climate",
            "set_temperature",
            {
                "entity_id": cmd.entity_id,
                "temperature": cmd.temperature,
                "hvac_mode": cmd.hvac_mode,
            },
        )
    if isinstance(cmd, SetFanMode):
        return (
            "climate",
            "set_fan_mode",
            {
                "entity_id": cmd.entity_id,
                "fan_mode": cmd.fan_mode,
            },
        )
    if isinstance(cmd, FanTurnOn):
        return "fan", "turn_on", {"entity_id": cmd.entity_id}
    if isinstance(cmd, FanTurnOff):
        return "fan", "turn_off", {"entity_id": cmd.entity_id}
    if isinstance(cmd, FanSetPreset):
        return (
            "fan",
            "set_preset_mode",
            {
                "entity_id": cmd.entity_id,
                "preset_mode": cmd.preset_mode,
            },
        )
    if isinstance(cmd, FanSetPercentage):
        return (
            "fan",
            "set_percentage",
            {
                "entity_id": cmd.entity_id,
                "percentage": cmd.percentage,
            },
        )
    if isinstance(cmd, FanSetDirection):
        if cmd.via_preset:
            preset = "reverse" if cmd.direction == "reverse" else cmd.forward_preset
            return (
                "fan",
                "set_preset_mode",
                {"entity_id": cmd.entity_id, "preset_mode": preset},
            )
        return (
            "fan",
            "set_direction",
            {
                "entity_id": cmd.entity_id,
                "direction": cmd.direction,
            },
        )
    if isinstance(cmd, SwitchTurnOn):
        return "switch", "turn_on", {"entity_id": cmd.entity_id}
    if isinstance(cmd, SwitchTurnOff):
        return "switch", "turn_off", {"entity_id": cmd.entity_id}
    msg = f"Unknown command {cmd!r}"
    raise ValueError(msg)


def _device_label(room: Room, entity_id: str) -> str:
    """Map a command's entity_id back to the room's device role, for CC-L7."""
    labels = {
        room.ac_climate: "A/C",
        room.heater_climate: "Heater",
        room.ac_fan_entity: "A/C fan",
        room.heater_fan_entity: "Heater fan",
        room.ac_power_switch: "A/C power",
        room.heater_power_switch: "Heater power",
    }
    for eid in room.fan_entities:
        labels[eid] = f"Fan {eid.split('.')[-1]}"
    return labels.get(entity_id) or entity_id


def _describe_command(cmd: Command, room: Room) -> str:  # noqa: PLR0911
    """Render a resolved command as a short "<device> <action>" phrase (CC-L7)."""
    label = _device_label(room, cmd.entity_id)
    if isinstance(cmd, SetHvacMode):
        return f"{label} → {cmd.hvac_mode}"
    if isinstance(cmd, TurnOffClimate):
        return f"{label} off"
    if isinstance(cmd, SetTemperature):
        return f"{label} setpoint → {cmd.temperature}°F"
    if isinstance(cmd, SetFanMode):
        return f"{label} fan speed → {cmd.fan_mode}"
    if isinstance(cmd, FanTurnOn):
        return f"{label} on"
    if isinstance(cmd, FanTurnOff):
        return f"{label} off"
    if isinstance(cmd, FanSetPreset):
        return f"{label} speed → {cmd.preset_mode}"
    if isinstance(cmd, FanSetPercentage):
        return f"{label} speed → {cmd.percentage}%"
    if isinstance(cmd, FanSetDirection):
        return f"{label} direction → {cmd.direction}"
    if isinstance(cmd, SwitchTurnOn):
        return f"{label} on"
    if isinstance(cmd, SwitchTurnOff):
        return f"{label} off"
    return repr(cmd)


def _threshold_context(room: Room, inputs: EngineInputs) -> str:
    """Compact room-temp + per-device threshold summary (CC-L7)."""
    parts = [f"temp {int(inputs.room_temp)}°F"]
    if room.has_ac:
        parts.append(
            f"cooling target {int(inputs.target_cooling)}°F "
            f"(med {int(inputs.cooling_medium)}°F high {int(inputs.cooling_high)}°F)"
        )
    if room.has_heater:
        parts.append(
            f"heating target {int(inputs.target_heating)}°F "
            f"(med {int(inputs.heating_medium)}°F high {int(inputs.heating_high)}°F)"
        )
    parts.extend(
        f"fan {fan.info.entity_id.split('.')[-1]} target {int(fan.target)}°F "
        f"(med {int(fan.medium)}°F high {int(fan.high)}°F)"
        for fan in inputs.fans
    )
    return "; ".join(parts)


class RoomController:
    """Reacts to a room's live entities and commands the physical devices."""

    def __init__(
        self, hass: HomeAssistant, entry: RoomClimateConfigEntry, room: Room
    ) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry = entry
        self.room = room
        self._unsub_state = None
        self._tracked: frozenset[str] = frozenset()
        self._task: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------
    @callback
    def async_start(self) -> None:
        """Subscribe and run an initial evaluation."""
        self._resubscribe()
        self.async_request_run(trigger="startup")
        self._log_capabilities()
        # Pick up our own entities that register a moment after setup.
        self.entry.async_on_unload(
            async_call_later(self.hass, 3, self._delayed_resubscribe)
        )
        self.entry.async_on_unload(self.async_stop)

    @callback
    def async_stop(self) -> None:
        """Cancel work and unsubscribe."""
        if self._task and not self._task.done():
            self._task.cancel()
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None

    @callback
    def _delayed_resubscribe(self, _now: object) -> None:
        self._resubscribe()
        self.async_request_run(trigger="startup")
        self._log_capabilities()

    def _log_capabilities(self) -> None:
        """
        Dump each configured climate/fan entity's capabilities at INFO (CC-L10).

        Called at startup and again after the delayed resubscribe, since an
        entity registered by another integration may not have a state yet when
        the controller first starts.
        """
        room = self.room
        if room.has_ac:
            _CAPABILITIES_LOGGER.info(
                "[room=%s] A/C capabilities: %s",
                room.key,
                describe_climate_capabilities(self.hass, room.ac_climate),
            )
            if room.ac_fan_entity:
                _CAPABILITIES_LOGGER.info(
                    "[room=%s] A/C fan capabilities: %s",
                    room.key,
                    describe_fan_capabilities(self.hass, room.ac_fan_entity),
                )
        if room.has_heater and not room.combined:
            _CAPABILITIES_LOGGER.info(
                "[room=%s] Heater capabilities: %s",
                room.key,
                describe_climate_capabilities(self.hass, room.heater_climate),
            )
            if room.heater_fan_entity:
                _CAPABILITIES_LOGGER.info(
                    "[room=%s] Heater fan capabilities: %s",
                    room.key,
                    describe_fan_capabilities(self.hass, room.heater_fan_entity),
                )
        if room.has_fan:
            for eid in room.fan_entities:
                _CAPABILITIES_LOGGER.info(
                    "[room=%s] Fan %s capabilities: %s",
                    room.key,
                    eid,
                    describe_fan_capabilities(self.hass, eid),
                )

    # -- subscriptions -------------------------------------------------------
    @callback
    def _resubscribe(self) -> None:
        ids = self._tracked_ids()
        if ids == self._tracked:
            return
        self._tracked = ids
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if ids:
            self._unsub_state = async_track_state_change_event(
                self.hass, list(ids), self._on_change
            )

    def _tracked_ids(self) -> frozenset[str]:
        """Temp/humidity sensors plus whichever room live entities currently resolve."""
        ids: set[str] = set()
        if self.room.temperature_sensor:
            ids.add(self.room.temperature_sensor)
        if self.room.humidity_sensor:
            ids.add(self.room.humidity_sensor)
        ids.update(self.room.window_sensors)
        for device in self.room.devices:
            if device == DEVICE_FAN:
                continue
            for key in (
                KEY_USE[device],
                KEY_TARGET[device],
                KEY_MEDIUM_OFFSET[device],
                KEY_HIGH_OFFSET[device],
            ):
                domain = "switch" if key.startswith("use_") else "number"
                if eid := self._resolve(key, domain):
                    ids.add(eid)
        if self.room.has_fan:
            fan_keys: list[tuple[str, str]] = [
                (KEY_MEDIUM_OFFSET["fan"], "number"),
                (KEY_HIGH_OFFSET["fan"], "number"),
            ]
            for fan_eid in self.room.fan_entities:
                slug = fan_slug(fan_eid)
                fan_keys += [
                    (fan_use_key(slug), "switch"),
                    (fan_target_key(slug), "number"),
                    (fan_reverse_key(slug), "switch"),
                ]
            for key, domain in fan_keys:
                if eid := self._resolve(key, domain):
                    ids.add(eid)
        for key in (
            KEY_MANUAL_MODE,
            KEY_AC_FAN_ONLY,
            KEY_HEATER_FAN_ONLY,
        ):
            if eid := self._resolve(key, "switch"):
                ids.add(eid)
        return frozenset(ids)

    @callback
    def _on_change(self, event: Event[EventStateChangedData]) -> None:
        data = event.data
        entity_id: str = data["entity_id"]
        old = data["old_state"]
        new = data["new_state"]
        old_val = old.state if old else None
        new_val = new.state if new else None
        changed = old_val != new_val
        if entity_id == self.room.humidity_sensor:
            # CC-L2: humidity must never command a device — the engine ignores
            # humidity entirely, so short-circuit unconditionally (regardless of
            # ``changed``) rather than resubscribing/requesting a run that could
            # flush an owed command from an unrelated change. Only log when the
            # value actually moved.
            if changed:
                _SENSOR_LOGGER.info(
                    "[room=%s] Humidity changed: %s → %s%%",
                    self.room.key,
                    old_val,
                    new_val,
                )
            return
        trigger = f"{entity_id} changed"
        if changed and entity_id == self.room.temperature_sensor:
            _SENSOR_LOGGER.info(
                "[room=%s] Temperature changed: %s → %s°F",
                self.room.key,
                old_val,
                new_val,
            )
            trigger = f"temperature {old_val}→{new_val}°F"
        elif changed and entity_id in self.room.window_sensors:
            state_label = "opened" if new_val == "on" else "closed"
            _SENSOR_LOGGER.info(
                "[room=%s] Window %s %s",
                self.room.key,
                entity_id,
                state_label,
            )
            trigger = f"window {entity_id} {state_label}"
        self._resubscribe()
        self.async_request_run(trigger=trigger)

    def _resolve_command(self, cmd: Command) -> Command:
        """
        Resolve a command against the device's *live* state before it is sent.

        Currently this clamps a ``SetTemperature`` into the device's reported
        ``min_temp``/``max_temp`` range (CC-9). Used by ``_run`` both at send
        time and when rendering the CC-L7 description, so the logged value
        always matches what is actually sent. ``_run`` resolves each command in
        order, after any preceding ``SetHvacMode`` in the same evaluation, so
        the live range read here reflects what the device will actually
        validate against.
        """
        if isinstance(cmd, SetTemperature):
            state = self.hass.states.get(cmd.entity_id)
            attrs = state.attributes if state else {}
            temperature = clamp_setpoint(
                cmd.temperature, attrs.get("min_temp"), attrs.get("max_temp")
            )
            if temperature != cmd.temperature:
                return replace(cmd, temperature=temperature)
        return cmd

    def _device_reports_setpoint(self, entity_id: str) -> bool:
        """Whether ``entity_id`` currently reports a ``temperature`` attribute."""
        state = self.hass.states.get(entity_id)
        return bool(state) and state.attributes.get("temperature") is not None

    # -- evaluation ----------------------------------------------------------
    @callback
    def async_request_run(self, trigger: str = "evaluation") -> None:
        """Restart the evaluation task (restart semantics, like the blueprint)."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = self.hass.async_create_task(
            self._run(trigger), f"{DOMAIN} control {self.room.key}"
        )

    async def _run(self, trigger: str = "evaluation") -> None:
        try:
            inputs = self._build_inputs()
            if inputs is None:
                return
            commands = compute_commands(inputs)
            action_descriptions: list[str] = []
            for cmd in commands:
                if isinstance(cmd, Delay):
                    await asyncio.sleep(cmd.ms / 1000)
                    continue
                # Resolve against the device's *live* range, read now rather
                # than from the build-time snapshot: an A/C reports a
                # mode-dependent range (off vs cool), and any preceding
                # SetHvacMode has already switched it, so this reflects the
                # range the device will actually validate against (CC-9).
                # Resolved once here and reused for both the service call and
                # the CC-L7 description, so the log always matches what was
                # actually sent.
                resolved_cmd = self._resolve_command(cmd)
                action_descriptions.append(_describe_command(resolved_cmd, self.room))
                domain, service, data = _service_for(resolved_cmd)
                # Isolate each call: one device rejecting a command (e.g. a
                # transient out-of-range setpoint) must not abandon the
                # remaining commands for this room.
                try:
                    await self.hass.services.async_call(
                        domain, service, data, blocking=True
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception(
                        "Room %s: command failed: %s.%s %s",
                        self.room.key,
                        domain,
                        service,
                        data,
                    )
            if action_descriptions:
                _LOGGER.info(
                    "[room=%s] RCC commanded: %s (trigger: %s; %s)",
                    self.room.key,
                    ", ".join(action_descriptions),
                    trigger,
                    _threshold_context(self.room, inputs),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Room %s: control evaluation failed", self.room.key)

    def _build_inputs(self) -> EngineInputs | None:
        """Read all live + device state; None if manual mode or invalid temp."""
        room = self.room
        # Default to manual (dormant) when the state can't be read, so a lost or
        # not-yet-restored switch state never silently re-activates control.
        if self._switch_state(KEY_MANUAL_MODE, default=True):
            return None
        room_temp = self._temperature()
        if room_temp is None:
            return None

        target_cooling = self._number(KEY_TARGET["cooling"], 72.0)
        target_heating = self._number(KEY_TARGET["heating"], 68.0)
        cool_med = self._number(KEY_MEDIUM_OFFSET["cooling"], 3.0)
        cool_high = self._number(KEY_HIGH_OFFSET["cooling"], 6.0)
        heat_med = self._number(KEY_MEDIUM_OFFSET["heating"], 3.0)
        heat_high = self._number(KEY_HIGH_OFFSET["heating"], 6.0)

        fan_med = self._number(KEY_MEDIUM_OFFSET["fan"], 3.0)
        fan_high = self._number(KEY_HIGH_OFFSET["fan"], 6.0)
        fans: list[FanControl] = []
        for eid in room.fan_entities:
            info = self._fan_info(eid)
            if info is None:
                continue  # physical fan not available yet; controlled when it returns
            slug = fan_slug(eid)
            target = self._number(fan_target_key(slug), 72.0)
            fans.append(
                FanControl(
                    info=info,
                    use=self._switch_state(fan_use_key(slug), default=False),
                    target=target,
                    medium=target + fan_med,
                    high=target + fan_high,
                    reverse=self._switch_state(fan_reverse_key(slug), default=False),
                )
            )

        return EngineInputs(
            combined=room.combined,
            room_temp=room_temp,
            ac=self._climate_info(room.ac_climate) if room.has_ac else None,
            heater=(
                self._climate_info(room.heater_climate)
                if room.has_heater and not room.combined
                else None
            ),
            fans=tuple(fans),
            ac_fan=self._fan_info(room.ac_fan_entity),
            heater_fan=self._fan_info(room.heater_fan_entity),
            ac_power=self._switch_info(room.ac_power_switch),
            heater_power=self._switch_info(room.heater_power_switch),
            use_ac=self._switch_state(KEY_USE["cooling"], default=False),
            use_heater=self._switch_state(KEY_USE["heating"], default=False),
            ac_fan_only_override=self._switch_state(KEY_AC_FAN_ONLY, default=False),
            heater_fan_only_override=self._switch_state(
                KEY_HEATER_FAN_ONLY, default=False
            ),
            target_cooling=target_cooling,
            cooling_medium=target_cooling + cool_med,
            cooling_high=target_cooling + cool_high,
            target_heating=target_heating,
            heating_medium=target_heating - heat_med,
            heating_high=target_heating - heat_high,
            command_delay_ms=int(room.command_delay * 1000),
            power_on_delay_ms=int(room.power_on_delay * 1000),
            window_open=self._window_open(),
        )

    # -- state readers -------------------------------------------------------
    def _resolve(self, key: str, domain: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, room_uid(self.entry.entry_id, self.room.key, key)
        )

    def _temperature(self) -> float | None:
        if not self.room.temperature_sensor:
            return None
        state = self.hass.states.get(self.room.temperature_sensor)
        if state is None or state.state in _INVALID:
            return None
        try:
            return float(state.state)
        except TypeError, ValueError:
            return None

    def _number(self, key: str, default: float) -> float:
        eid = self._resolve(key, "number")
        if eid and (state := self.hass.states.get(eid)) and state.state not in _INVALID:
            try:
                return float(state.state)
            except TypeError, ValueError:
                return default
        return default

    def _switch_state(self, key: str, *, default: bool) -> bool:
        eid = self._resolve(key, "switch")
        if eid and (state := self.hass.states.get(eid)):
            return state.state == STATE_ON
        return default

    def _window_open(self) -> bool:
        """
        Return True if ANY of the room's window sensors reads open (CC-20).

        Missing/unavailable/unknown sensors are treated as closed (CC-21); the
        aggregation + fail-safe live in the pure engine helper.
        """
        states = (self.hass.states.get(eid) for eid in self.room.window_sensors)
        return any_window_open(s.state if s else None for s in states)

    def _climate_info(self, entity_id: str | None) -> ClimateInfo | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        attrs = state.attributes
        features = int(attrs.get("supported_features") or 0)
        raw_setpoint = attrs.get("temperature")
        return ClimateInfo(
            entity_id=entity_id,
            hvac_mode=attrs.get("hvac_mode") or state.state,
            fan_mode=attrs.get("fan_mode"),
            hvac_modes=tuple(attrs.get("hvac_modes") or ()),
            fan_modes=tuple(attrs.get("fan_modes") or ()),
            min_temp=attrs.get("min_temp"),
            max_temp=attrs.get("max_temp"),
            supports_set_temp=bool(features & 1),
            current_setpoint=float(raw_setpoint) if raw_setpoint is not None else None,
        )

    def _fan_info(self, entity_id: str | None) -> FanInfo | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        attrs = state.attributes
        preset_modes = tuple(attrs.get("preset_modes") or ())
        via_preset = fan_direction_via_preset(self.hass, entity_id)
        if via_preset:
            forward_preset = next((p for p in preset_modes if p != "reverse"), "normal")
            direction = (
                "reverse" if attrs.get("preset_mode") == "reverse" else "forward"
            )
        else:
            forward_preset = "normal"
            direction = attrs.get("direction")
        return FanInfo(
            entity_id=entity_id,
            is_on=state.state == STATE_ON,
            preset_mode=attrs.get("preset_mode"),
            percentage=int(attrs.get("percentage") or 0),
            preset_modes=preset_modes,
            percentage_step=float(attrs.get("percentage_step") or 1.0),
            reversible=fan_supports_direction(self.hass, entity_id),
            direction=direction,
            direction_via_preset=via_preset,
            forward_preset=forward_preset,
        )

    def _switch_info(self, entity_id: str | None) -> SwitchInfo | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return SwitchInfo(entity_id=entity_id, is_on=state.state == STATE_ON)
