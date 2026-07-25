"""
Pure reactive-control engine (no Home Assistant imports).

``compute_commands(inputs)`` is a pure function of the current room/device state
that returns the ordered list of device commands to issue. The HA-coupled
``controller.py`` gathers state into :class:`EngineInputs`, calls this, and
executes the returned commands. Keeping it pure makes the control logic
unit-testable with plain ``python3``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, Final

from .fan_logic import cooling_speed, heating_speed, match_fan_mode

if TYPE_CHECKING:
    from collections.abc import Iterable

COOL = "cool"
HEAT = "heat"
FAN_ONLY = "fan_only"
OFF = "off"

FORWARD = "forward"
REVERSE = "reverse"

_OFF_LIKE = frozenset({"off", "unavailable", "unknown", "none", "", None})

# Hysteresis deadband (CC-27): the on/off decision is *not* a bare truncated
# threshold. Once a device is conditioning it keeps going until the room comes
# within HYSTERESIS_OFF of the target; once stopped it does not restart until
# the room passes the next whole degree past the target. The device's reported
# running mode (COOL/HEAT/fan on) is the hysteresis state — the engine stays
# stateless. Setpoints and fan-speed tiers still truncate (CC-5). The humidity
# fan trigger (CC-30) uses the same shape with its own wider %RH band.
HYSTERESIS_OFF: Final = 0.2
HYSTERESIS_ON: Final = 1.0

HUMIDITY_HYSTERESIS_OFF: Final = 0.5
HUMIDITY_HYSTERESIS_ON: Final = 2.0

MAX_PERCENTAGE: Final = 100.0


def _wants_cool(room: float, target: float, running: bool) -> bool:  # noqa: FBT001
    """Cooling-style hysteresis (CC-27): hold near target, restart a degree past it."""
    if running:
        return room > target + HYSTERESIS_OFF
    return room >= target + HYSTERESIS_ON


def _wants_fan_for_humidity(
    humidity: float,
    target: float,
    running: bool,  # noqa: FBT001
) -> bool:
    """Humidity hysteresis (CC-30): cooling-style, wider band for noisy %RH."""
    if running:
        return humidity > target + HUMIDITY_HYSTERESIS_OFF
    return humidity >= target + HUMIDITY_HYSTERESIS_ON


def _wants_heat(room: float, target: float, running: bool) -> bool:  # noqa: FBT001
    """Heating hysteresis (CC-27): mirror of :func:`_wants_cool`."""
    if running:
        return room < target - HYSTERESIS_OFF
    return room <= target - HYSTERESIS_ON


def any_window_open(states: Iterable[str | None]) -> bool:
    """
    Return whether a room counts as "window open" given its sensor states (CC-20).

    A room with multiple window sensors is open if **any** one reads ``"on"``.
    Fail-safe (CC-21): a missing/``None``/``unavailable``/``unknown`` reading is
    treated as closed, so bad or absent sensor data never suppresses
    conditioning. A room with no sensors is therefore closed.
    """
    return any(state == "on" for state in states)


def clamp_setpoint(value: int, min_temp: float | None, max_temp: float | None) -> int:
    """
    Clamp a whole-°F setpoint into a device's accepted range, with a 1° margin.

    The margin absorbs °F/°C display rounding (CC-9): a device with a whole-°C
    limit reports it as a whole °F that can be up to 0.5° off, so the *raw*
    bound can convert back out of range — e.g. 18 °C reports as 64 °F, but
    64 °F = 17.78 °C < 18 °C and is rejected, while 65 °F = 18.33 °C is fine.
    Pulling each bound 1° inward guarantees the value survives the round-trip
    in either direction; the cost is at most 1 °F off the absolute extreme,
    which is immaterial because comfort is driven by fan speed.
    """
    if max_temp is not None:
        value = min(value, int(max_temp) - 1)
    if min_temp is not None:
        value = max(value, int(min_temp) + 1)
    return value


def _setpoint_needs_send(
    current_setpoint: float | None,
    desired_setpoint: int,
) -> bool:
    """
    Whether a ``SetTemperature`` is needed for the current evaluation (CC-19).

    A device that doesn't report its setpoint (``current_setpoint is None``)
    can never be confirmed to have converged, so it is always (re)sent —
    mirroring CC-23's "unknown never matches" convention for fan direction.
    The controller logs that the device is non-reporting so this is
    distinguishable from a genuine setpoint mismatch.
    """
    return current_setpoint is None or current_setpoint != desired_setpoint


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ClimateInfo:
    """Current state + capabilities of a climate entity."""

    entity_id: str
    hvac_mode: str
    fan_mode: str | None
    hvac_modes: tuple[str, ...]
    fan_modes: tuple[str, ...]
    min_temp: float | None
    supports_set_temp: bool
    current_setpoint: float | None = None
    max_temp: float | None = None

    @property
    def has_fan(self) -> bool:
        """Treat fan_only support or any fan_modes as fan-capable."""
        return "fan_only" in self.hvac_modes or bool(self.fan_modes)


@dataclass(frozen=True)
class FanInfo:
    """Current state of a fan entity."""

    entity_id: str
    is_on: bool
    preset_mode: str | None
    percentage: int
    preset_modes: tuple[str, ...]
    # Device speed-grid step (CC-6): HA snaps a commanded percentage up (ceil)
    # to the nearest of ``round(100 / percentage_step)`` discrete speeds, then
    # reports that snapped value back. 1.0 means continuous (no snapping).
    percentage_step: float = 1.0
    # Direction support (standalone ceiling fans, CC-22..CC-25). ``reversible``
    # mirrors the entity's DIRECTION capability or "reverse" preset presence;
    # ``direction`` is the reported "forward"/"reverse", or None when unknown.
    # ``direction_via_preset`` is True when direction is set via preset_mode
    # rather than fan.set_direction; ``forward_preset`` is the preset name to
    # use when the engine wants to restore forward direction on such fans.
    reversible: bool = False
    direction: str | None = None
    direction_via_preset: bool = False
    forward_preset: str = "normal"


@dataclass(frozen=True)
class FanControl:
    """A single standalone fan's live state plus its resolved control values."""

    info: FanInfo
    use: bool
    target: float
    medium: float  # target + shared medium offset (absolute threshold)
    high: float  # target + shared high offset (absolute threshold)
    reverse: bool


@dataclass(frozen=True)
class SwitchInfo:
    """Current state of a power switch."""

    entity_id: str
    is_on: bool


@dataclass(frozen=True)
class EngineInputs:
    """Everything the decision needs — config, live values, and device state."""

    combined: bool
    room_temp: float
    # devices (None when the room lacks them)
    ac: ClimateInfo | None
    heater: ClimateInfo | None
    ac_fan: FanInfo | None
    heater_fan: FanInfo | None
    fans: tuple[FanControl, ...]
    ac_power: SwitchInfo | None
    heater_power: SwitchInfo | None
    # use toggles + overrides
    use_ac: bool
    use_heater: bool
    ac_fan_only_override: bool
    heater_fan_only_override: bool
    # setpoints / thresholds
    target_cooling: float
    cooling_medium: float
    cooling_high: float
    target_heating: float
    heating_medium: float
    heating_high: float
    command_delay_ms: int
    power_on_delay_ms: int
    # window sensor: True when the room's window is open. Suppresses active
    # cooling/heating (Cool/Heat) only — fan-only circulation is unaffected.
    window_open: bool = False
    # Room-level humidity trigger (standalone fans only). All None ⇒ ignored
    # (no humidity sensor, sensor unreadable, or room has no fans).
    room_humidity: float | None = None
    humidity_target: float | None = None
    humidity_medium: float | None = None  # absolute: target + medium offset
    humidity_high: float | None = None  # absolute: target + high offset

    # derived helpers ----------------------------------------------------
    @property
    def has_ac(self) -> bool:
        """Return True when an AC climate entity is configured."""
        return self.ac is not None

    @property
    def has_heater(self) -> bool:
        """Return True when a heater climate entity is configured."""
        return self.heater is not None

    @property
    def has_fan(self) -> bool:
        """Return True when the room has one or more standalone fans."""
        return bool(self.fans)

    @property
    def use_fan(self) -> bool:
        """Whether any standalone fan's Use toggle is on (CC-12 fan-only override)."""
        return any(f.use for f in self.fans)

    @property
    def ac_setpoint_int(self) -> int:
        """Return the AC climate setpoint (device min or 65 °F floor)."""
        # The engine controls comfort via fan speed, so the climate's own target
        # is driven to its lowest settable value (max cooling), or 65 °F when the
        # device doesn't report a min_temp. The controller re-clamps this to the
        # device's *live* range at send time (see clamp_setpoint / CC-9), since
        # the range can be mode-dependent and reported in whole °F rounded from
        # the device's native unit.
        min_temp = self.ac.min_temp if self.ac else None
        return round(min_temp if min_temp is not None else 65)

    @property
    def target_heating_int(self) -> int:
        """Return the heating target truncated to whole degrees."""
        return int(self.target_heating)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Command:
    """Base class for an emitted device command."""


@dataclass(frozen=True)
class Delay(Command):
    """Pause between commands (milliseconds)."""

    ms: int


@dataclass(frozen=True)
class SetHvacMode(Command):
    """Set the HVAC mode on a climate entity."""

    entity_id: str
    hvac_mode: str


@dataclass(frozen=True)
class TurnOffClimate(Command):
    """Turn off a climate entity."""

    entity_id: str


@dataclass(frozen=True)
class SetTemperature(Command):
    """Set the target temperature on a climate entity."""

    entity_id: str
    temperature: int
    hvac_mode: str


@dataclass(frozen=True)
class SetFanMode(Command):
    """Set the fan mode on a climate entity."""

    entity_id: str
    fan_mode: str


@dataclass(frozen=True)
class FanTurnOn(Command):
    """Turn on a fan entity."""

    entity_id: str


@dataclass(frozen=True)
class FanTurnOff(Command):
    """Turn off a fan entity."""

    entity_id: str


@dataclass(frozen=True)
class FanSetPreset(Command):
    """Set the preset mode on a fan entity."""

    entity_id: str
    preset_mode: str


@dataclass(frozen=True)
class FanSetPercentage(Command):
    """Set the speed percentage on a fan entity."""

    entity_id: str
    percentage: int


@dataclass(frozen=True)
class FanSetDirection(Command):
    """Set the spin direction ("forward"/"reverse") on a fan entity."""

    entity_id: str
    direction: str
    # When True the controller uses fan.set_preset_mode instead of
    # fan.set_direction (for fans that express direction as a preset, e.g. Dreo).
    via_preset: bool = False
    # Preset name to restore when direction="forward" on a via_preset fan.
    forward_preset: str = "normal"


@dataclass(frozen=True)
class SwitchTurnOn(Command):
    """Turn on a switch entity."""

    entity_id: str


@dataclass(frozen=True)
class SwitchTurnOff(Command):
    """Turn off a switch entity."""

    entity_id: str


@dataclass
class _Out:
    """Mutable command accumulator."""

    items: list[Command] = field(default_factory=list)

    def add(self, *cmds: Command) -> None:
        self.items.extend(cmds)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def compute_commands(inp: EngineInputs) -> list[Command]:
    """
    Return the ordered commands for the current state.

    Manual-mode gating is the controller's job; this assumes control is active.
    """
    out = _Out()
    if inp.combined and inp.ac is not None:
        _combined(inp, out)
    else:
        if inp.ac is not None:
            _split_ac(inp, out)
        if inp.heater is not None:
            _split_heater(inp, out)
    for fan in inp.fans:
        _standalone_fan(fan, inp, out)
    return out.items


# ---------------------------------------------------------------------------
# Combined heat-pump branch
# ---------------------------------------------------------------------------
def _combined(inp: EngineInputs, out: _Out) -> None:  # noqa: PLR0912
    ac = inp.ac
    assert ac is not None
    needs_cool = (
        inp.use_ac
        and not inp.window_open
        and _wants_cool(inp.room_temp, inp.target_cooling, ac.hvac_mode == COOL)
    )
    needs_heat = (
        inp.use_heater
        and not inp.window_open
        and _wants_heat(inp.room_temp, inp.target_heating, ac.hvac_mode == HEAT)
    )
    uses_disabled = not inp.use_ac and not inp.use_heater
    ac_override_fan_only = (
        inp.ac_fan_only_override
        and ac.has_fan
        and (
            (inp.use_ac and not needs_cool)
            or (not inp.use_ac and (not inp.has_fan or inp.use_fan))
        )
    )
    native_heater_fan_only = (
        inp.use_heater and ac.has_fan and not needs_heat and not uses_disabled
    )
    if uses_disabled:
        use_fan_only = (
            inp.ac_fan_only_override and ac.has_fan and (not inp.has_fan or inp.use_fan)
        )
    else:
        use_fan_only = ac_override_fan_only or native_heater_fan_only

    if uses_disabled and not use_fan_only:
        decision = OFF
    elif needs_cool:
        decision = COOL
    elif needs_heat:
        decision = HEAT
    elif use_fan_only:
        decision = FAN_ONLY
    else:
        decision = OFF

    target = inp.target_heating_int if decision == HEAT else inp.ac_setpoint_int
    current = ac.hvac_mode

    if inp.ac_power and decision in (COOL, HEAT, FAN_ONLY) and not inp.ac_power.is_on:
        out.add(SwitchTurnOn(inp.ac_power.entity_id), Delay(inp.power_on_delay_ms))
    if decision not in {OFF, current}:
        out.add(SetHvacMode(ac.entity_id, decision), Delay(inp.command_delay_ms))
    if decision == OFF and current not in _OFF_LIKE:
        out.add(
            SetHvacMode(ac.entity_id, OFF),
            Delay(inp.command_delay_ms),
            TurnOffClimate(ac.entity_id),
        )
    desired_setpoint = clamp_setpoint(target, ac.min_temp, ac.max_temp)
    if (
        decision != OFF
        and ac.supports_set_temp
        and _setpoint_needs_send(ac.current_setpoint, desired_setpoint)
    ):
        out.add(SetTemperature(ac.entity_id, target, decision))
    if inp.ac_power and decision == OFF and inp.ac_power.is_on:
        out.add(Delay(inp.command_delay_ms), SwitchTurnOff(inp.ac_power.entity_id))

    if ac.fan_modes and decision != OFF:
        label = _combined_speed_label(inp, decision)
        matched = match_fan_mode(ac.fan_modes, label)
        if matched and ac.fan_mode != matched:
            out.add(Delay(inp.command_delay_ms), SetFanMode(ac.entity_id, matched))


def _combined_speed_label(inp: EngineInputs, decision: str) -> str:
    """Pick cooling vs heating tiers for the combined climate fan."""
    if decision == HEAT:
        return heating_speed(inp.room_temp, inp.heating_medium, inp.heating_high)[0]
    if decision == COOL:
        return cooling_speed(inp.room_temp, inp.cooling_medium, inp.cooling_high)[0]
    if decision == FAN_ONLY:
        if inp.use_heater and not inp.use_ac:
            return heating_speed(inp.room_temp, inp.heating_medium, inp.heating_high)[0]
        # use_ac, or neither use selected: fall back to cooling tiers.
        return cooling_speed(inp.room_temp, inp.cooling_medium, inp.cooling_high)[0]
    return "low"


# ---------------------------------------------------------------------------
# Split A/C branch
# ---------------------------------------------------------------------------
def _split_ac(inp: EngineInputs, out: _Out) -> None:
    ac = inp.ac
    assert ac is not None
    needs_cool = (
        inp.use_ac
        and not inp.window_open
        and _wants_cool(inp.room_temp, inp.target_cooling, ac.hvac_mode == COOL)
    )
    override_fan_only = (
        inp.ac_fan_only_override
        and ac.has_fan
        and (
            (inp.use_ac and not needs_cool)
            or (not inp.use_ac and (not inp.has_fan or inp.use_fan))
        )
    )
    decision = COOL if needs_cool else (FAN_ONLY if override_fan_only else OFF)
    current = ac.hvac_mode

    if inp.ac_power and decision in (COOL, FAN_ONLY) and not inp.ac_power.is_on:
        out.add(SwitchTurnOn(inp.ac_power.entity_id), Delay(inp.power_on_delay_ms))
    if decision not in {OFF, current}:
        out.add(SetHvacMode(ac.entity_id, decision), Delay(inp.command_delay_ms))
    if decision == OFF and current not in _OFF_LIKE:
        out.add(
            SetHvacMode(ac.entity_id, OFF),
            Delay(inp.command_delay_ms),
            TurnOffClimate(ac.entity_id),
        )
    desired_setpoint = clamp_setpoint(inp.ac_setpoint_int, ac.min_temp, ac.max_temp)
    if (
        decision in (COOL, FAN_ONLY)
        and ac.supports_set_temp
        and _setpoint_needs_send(ac.current_setpoint, desired_setpoint)
    ):
        out.add(SetTemperature(ac.entity_id, inp.ac_setpoint_int, decision))
    if inp.ac_power and decision == OFF and inp.ac_power.is_on:
        out.add(SwitchTurnOff(inp.ac_power.entity_id))

    has_fan_modes = bool(ac.fan_modes)
    if not (has_fan_modes or inp.ac_fan):
        return
    if decision in (COOL, FAN_ONLY):
        out.add(Delay(inp.command_delay_ms))
        label, percent = cooling_speed(
            inp.room_temp, inp.cooling_medium, inp.cooling_high
        )
        matched = match_fan_mode(ac.fan_modes, label)
        _emit_climate_fan(
            out, ac, has_fan_modes, matched, inp.ac_fan, label, percent, inp
        )
    elif decision == OFF and inp.ac_fan and inp.ac_fan.is_on:
        out.add(FanTurnOff(inp.ac_fan.entity_id))


# ---------------------------------------------------------------------------
# Split heater branch
# ---------------------------------------------------------------------------
def _split_heater(inp: EngineInputs, out: _Out) -> None:
    heater = inp.heater
    assert heater is not None
    needs_heat = (
        inp.use_heater
        and not inp.window_open
        and _wants_heat(inp.room_temp, inp.target_heating, heater.hvac_mode == HEAT)
    )
    native_fan_only = inp.use_heater and not needs_heat and heater.has_fan
    override_fan_only = (
        inp.heater_fan_only_override
        and heater.has_fan
        and (
            (inp.use_heater and not needs_heat)
            or (not inp.use_heater and (not inp.has_fan or inp.use_fan))
        )
    )
    decision = (
        HEAT
        if needs_heat
        else (FAN_ONLY if (native_fan_only or override_fan_only) else OFF)
    )
    current = heater.hvac_mode

    if inp.heater_power and decision in (HEAT, FAN_ONLY) and not inp.heater_power.is_on:
        out.add(SwitchTurnOn(inp.heater_power.entity_id), Delay(inp.power_on_delay_ms))
    if decision not in {OFF, current}:
        out.add(SetHvacMode(heater.entity_id, decision), Delay(inp.command_delay_ms))
    if decision == OFF and current != OFF:
        out.add(
            SetHvacMode(heater.entity_id, OFF),
            Delay(inp.command_delay_ms),
            TurnOffClimate(heater.entity_id),
        )
    desired_setpoint = clamp_setpoint(
        inp.target_heating_int, heater.min_temp, heater.max_temp
    )
    if (
        decision in (HEAT, FAN_ONLY)
        and heater.supports_set_temp
        and _setpoint_needs_send(heater.current_setpoint, desired_setpoint)
    ):
        out.add(SetTemperature(heater.entity_id, inp.target_heating_int, decision))
    if inp.heater_power and decision == OFF and inp.heater_power.is_on:
        out.add(SwitchTurnOff(inp.heater_power.entity_id))

    has_fan_modes = bool(heater.fan_modes)
    if not (has_fan_modes or inp.heater_fan):
        return
    if decision in (HEAT, FAN_ONLY):
        out.add(Delay(inp.command_delay_ms))
        label, percent = heating_speed(
            inp.room_temp, inp.heating_medium, inp.heating_high
        )
        matched = match_fan_mode(heater.fan_modes, label)
        _emit_climate_fan(
            out, heater, has_fan_modes, matched, inp.heater_fan, label, percent, inp
        )
    elif decision == OFF and inp.heater_fan and inp.heater_fan.is_on:
        out.add(FanTurnOff(inp.heater_fan.entity_id))
    elif decision == OFF and has_fan_modes and current != OFF:
        out.add(SetHvacMode(heater.entity_id, OFF))


def _emit_climate_fan(  # noqa: PLR0913
    out: _Out,
    climate: ClimateInfo,
    has_fan_modes: bool,  # noqa: FBT001
    matched: str,
    companion: FanInfo | None,
    label: str,
    percent: int,
    inp: EngineInputs,
) -> None:
    """Set climate fan_mode if available, else drive a companion fan entity."""
    if has_fan_modes and matched:
        if climate.fan_mode != matched:
            out.add(SetFanMode(climate.entity_id, matched))
        return
    if companion is None:
        return
    if label in companion.preset_modes:
        if not companion.is_on:
            out.add(FanTurnOn(companion.entity_id), Delay(inp.command_delay_ms))
        if (companion.preset_mode or "").lower() != label:
            out.add(FanSetPreset(companion.entity_id, label))
    else:
        if not companion.is_on:
            out.add(FanTurnOn(companion.entity_id), Delay(inp.command_delay_ms))
        if not _same_fan_speed(
            companion.percentage, percent, companion.percentage_step
        ):
            out.add(FanSetPercentage(companion.entity_id, percent))


def _same_fan_speed(reported: int, target: int, step: float) -> bool:
    """
    Check if reported fan speed aligns with the commanded speed grid (CC-6).

    HA snaps a ``set_percentage`` call up (``ceil``) onto a fan's discrete
    speed grid and reports back the snapped value, which a stepped fan can
    never report as the raw tier percentage (10/50/100). Comparing by grid
    index instead of raw percentage makes the de-dup idempotent, so the fan
    isn't re-commanded on every evaluation.
    """
    if step <= 1.0 or step >= MAX_PERCENTAGE:
        return reported == target
    count = round(MAX_PERCENTAGE / step)
    desired = min(max(ceil(target / MAX_PERCENTAGE * count), 1), count)
    current = min(max(round(reported / MAX_PERCENTAGE * count), 0), count)
    return current == desired


# ---------------------------------------------------------------------------
# Standalone fan (shared by both branches)
# ---------------------------------------------------------------------------
def _standalone_fan(fan: FanControl, inp: EngineInputs, out: _Out) -> None:
    info = fan.info
    # CC-13/CC-27: run when Use fan on and past the fan threshold (cooling-style
    # hysteresis keyed on the fan's own reported on/off state). CC-28/CC-29:
    # room humidity is an independent trigger — the fan runs when *either*
    # trigger wants it on, and stops only when both decline. The humidity
    # trigger is inactive unless a reading and all its thresholds resolved.
    temp_wants = _wants_cool(inp.room_temp, fan.target, info.is_on)
    hum_active = (
        inp.room_humidity is not None
        and inp.humidity_target is not None
        and inp.humidity_medium is not None
        and inp.humidity_high is not None
    )
    hum_wants = hum_active and _wants_fan_for_humidity(
        inp.room_humidity, inp.humidity_target, info.is_on
    )
    needs_on = fan.use and (temp_wants or hum_wants)
    if not needs_on:
        if info.is_on:
            out.add(FanTurnOff(info.entity_id))
        return
    # CC-14: speed follows the cooling-style tiers against this fan's thresholds.
    # CC-29: with humidity active the faster of the two ladders wins.
    label, percent = cooling_speed(inp.room_temp, fan.medium, fan.high)
    if hum_active:
        h_label, h_percent = cooling_speed(
            inp.room_humidity, inp.humidity_medium, inp.humidity_high
        )
        if h_percent > percent:
            label, percent = h_label, h_percent
    if not info.is_on:
        out.add(FanTurnOn(info.entity_id), Delay(inp.command_delay_ms))
    if label in info.preset_modes:
        if (info.preset_mode or "").lower() != label:
            out.add(FanSetPreset(info.entity_id, label))
    elif not _same_fan_speed(info.percentage, percent, info.percentage_step):
        out.add(FanSetPercentage(info.entity_id, percent))
    # Direction (CC-22..CC-24): only reversible fans, only while running, and
    # only when the reported direction differs (unknown never matches).
    if info.reversible:
        desired = REVERSE if fan.reverse else FORWARD
        if info.direction != desired:
            out.add(
                FanSetDirection(
                    info.entity_id,
                    desired,
                    via_preset=info.direction_via_preset,
                    forward_preset=info.forward_preset,
                )
            )
