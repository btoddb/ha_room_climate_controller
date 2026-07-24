"""
Tests for the pure reactive engine and fan logic.

These modules have no Home Assistant imports, so the test loads them directly
(via the ``_load`` shim, bypassing the package ``__init__``) and runs under plain
``pytest`` — no HA test harness required::

    pytest custom_components/btoddb_room_climate_controller/tests/
"""

import importlib.util
import pathlib
import sys
import types

_PKG = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str):
    """Load a pure module under a throwaway package so relative imports work."""
    if "rc_pure" not in sys.modules:
        pkg = types.ModuleType("rc_pure")
        pkg.__path__ = [str(_PKG)]
        sys.modules["rc_pure"] = pkg
    spec = importlib.util.spec_from_file_location(
        f"rc_pure.{name}", _PKG / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"rc_pure.{name}"] = module
    spec.loader.exec_module(module)
    return module


fan_logic = _load("fan_logic")
engine = _load("engine")

from rc_pure.engine import (  # noqa: E402
    ClimateInfo,
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
    SwitchTurnOn,
    any_window_open,
    clamp_setpoint,
    compute_commands,
)


def _types(cmds):
    return [type(c).__name__ for c in cmds]


def _base(**kw):
    defaults = dict(
        combined=False,
        room_temp=72.0,
        ac=None,
        heater=None,
        ac_fan=None,
        heater_fan=None,
        fans=(),
        ac_power=None,
        heater_power=None,
        use_ac=False,
        use_heater=False,
        ac_fan_only_override=False,
        heater_fan_only_override=False,
        target_cooling=72.0,
        cooling_medium=75.0,
        cooling_high=78.0,
        target_heating=68.0,
        heating_medium=65.0,
        heating_high=62.0,
        command_delay_ms=2000,
        power_on_delay_ms=3000,
    )
    defaults.update(kw)
    return EngineInputs(**defaults)


def _fan_control(
    entity_id="fan.tower",
    *,
    use=True,
    is_on=False,
    target=72.0,
    medium=75.0,
    high=78.0,
    reverse=False,
    preset_modes=(),
    percentage=0,
    preset_mode=None,
    percentage_step=1.0,
    reversible=False,
    direction=None,
):
    """Build a FanControl for a single standalone fan (see FanInfo field order)."""
    return FanControl(
        info=FanInfo(
            entity_id,
            is_on,
            preset_mode,
            percentage,
            tuple(preset_modes),
            percentage_step,
            reversible,
            direction,
        ),
        use=use,
        target=target,
        medium=medium,
        high=high,
        reverse=reverse,
    )


def _climate(
    hvac="off",
    fan_mode=None,
    fan_modes=(),
    min_temp=62.0,
    max_temp=None,
    set_temp=True,
    hvac_modes=("off", "cool"),
    current_setpoint=None,
):
    return ClimateInfo(
        entity_id="climate.ac",
        hvac_mode=hvac,
        fan_mode=fan_mode,
        hvac_modes=hvac_modes,
        fan_modes=tuple(fan_modes),
        min_temp=min_temp,
        max_temp=max_temp,
        supports_set_temp=set_temp,
        current_setpoint=current_setpoint,
    )


# --- fan_logic --------------------------------------------------------------
def test_cooling_tiers():
    assert fan_logic.cooling_speed(70, 75, 78) == ("low", 10)
    assert fan_logic.cooling_speed(76, 75, 78) == ("medium", 50)
    assert fan_logic.cooling_speed(80, 75, 78) == ("high", 100)


def test_heating_tiers():
    assert fan_logic.heating_speed(60, 65, 62) == ("high", 100)
    assert fan_logic.heating_speed(64, 65, 62) == ("medium", 50)
    assert fan_logic.heating_speed(70, 65, 62) == ("low", 10)


def test_fan_mode_matching():
    assert fan_logic.match_fan_mode(["low", "medium", "high"], "high") == "high"
    assert fan_logic.match_fan_mode(["quiet", "strong"], "low") == "quiet"
    assert fan_logic.match_fan_mode(["quiet", "strong"], "high") == "strong"
    assert fan_logic.match_fan_mode(["medium_low"], "medium") == "medium_low"
    # "high" must not match "medium_high"
    assert fan_logic.match_fan_mode(["medium_high"], "high") == ""
    assert fan_logic.match_fan_mode(["auto"], "low") == ""


# --- engine -----------------------------------------------------------------
def test_clamp_setpoint_celsius_round_trip():
    """
    CC-9: clamp pulls each bound 1° inward to survive °F/°C display rounding.

    A Matter A/C in cool mode reports min 64 °F / max 90 °F (really 18/32 °C).
    Sending 64 °F converts to 17.78 °C and is rejected, so the engine's
    "drive to min" target (45) must clamp to 65, and an over-max target to 89.
    """
    assert clamp_setpoint(45, 64, 90) == 65
    assert clamp_setpoint(100, 64, 90) == 89
    # A value already safely inside the range is left untouched.
    assert clamp_setpoint(72, 64, 90) == 72


def test_clamp_setpoint_passthrough_without_bounds():
    """Clamp is a no-op when the device reports no min/max."""
    assert clamp_setpoint(45, None, None) == 45
    assert clamp_setpoint(45, None, 90) == 45
    assert clamp_setpoint(100, 64, None) == 100


def test_split_ac_cool_with_fan_high():
    cmds = compute_commands(
        _base(
            ac=_climate(fan_modes=("low", "medium", "high")),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert _types(cmds) == [
        "SetHvacMode",
        "Delay",
        "SetTemperature",
        "Delay",
        "SetFanMode",
    ]
    assert cmds[0].hvac_mode == "cool"
    # A/C is driven to its lowest settable temp (min_temp=62 here), not a 65 floor.
    assert isinstance(cmds[2], SetTemperature) and cmds[2].temperature == 62
    assert isinstance(cmds[4], SetFanMode) and cmds[4].fan_mode == "high"


def test_split_ac_off_when_use_off():
    cmds = compute_commands(_base(ac=_climate(hvac="cool"), use_ac=False))
    assert _types(cmds) == ["SetHvacMode", "Delay", "TurnOffClimate"]
    assert cmds[0].hvac_mode == "off"


def test_split_ac_already_off_emits_nothing():
    """CC-19: an A/C already off is not re-commanded off on every evaluation."""
    cmds = compute_commands(_base(ac=_climate(hvac="off"), use_ac=False))
    assert cmds == []


def test_idle_room_with_off_switch_and_fans_emits_nothing():
    """
    CC-19: an idle room re-issues no turn-offs to already-off switch/fans.

    Reproduces the 'office' case: split A/C off with an off power switch, an off
    companion fan, and an off standalone fan must produce no commands on a
    sub-degree sensor change.
    """

    def off_fan(eid):
        return FanInfo(
            eid,
            is_on=False,
            preset_mode=None,
            percentage=0,
            preset_modes=("low", "high"),
        )

    cmds = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high")),
            ac_power=SwitchInfo("switch.ac_power", is_on=False),
            ac_fan=off_fan("fan.ac"),
            fans=(
                _fan_control("fan.ceiling", use=False, preset_modes=("low", "high")),
            ),
            use_ac=False,
            room_temp=73.0,
        )
    )
    assert cmds == []


def test_split_heater_already_off_emits_nothing():
    """CC-19: a heater already off is not re-commanded off on every evaluation."""
    cmds = compute_commands(
        _base(
            heater=_climate(hvac="off", hvac_modes=("off", "heat")),
            use_heater=False,
        )
    )
    assert cmds == []


def test_ac_fan_only_override():
    cmds = compute_commands(
        _base(
            ac=_climate(
                hvac_modes=("off", "cool", "fan_only"), fan_modes=("low", "high")
            ),
            use_ac=True,
            room_temp=70.0,
            ac_fan_only_override=True,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "fan_only" for c in cmds)


def test_power_switch_gating():
    cmds = compute_commands(
        _base(
            ac=_climate(),
            ac_power=SwitchInfo("switch.ac_power", is_on=False),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert isinstance(cmds[0], SwitchTurnOn)


def test_combined_heat_pump_heats():
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(hvac_modes=("off", "cool", "heat"), fan_modes=("low", "high")),
            use_ac=True,
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in cmds)
    assert any(isinstance(c, SetTemperature) and c.temperature == 68 for c in cmds)


def test_standalone_fan_medium():
    cmds = compute_commands(
        _base(
            fans=(_fan_control("fan.tower", preset_modes=("low", "medium", "high")),),
            room_temp=76.0,
        )
    )
    assert _types(cmds) == ["FanTurnOn", "Delay", "FanSetPreset"]
    assert isinstance(cmds[2], FanSetPreset) and cmds[2].preset_mode == "medium"


# --- whole-degree threshold-crossing accuracy (CC-5 / CC-L1) ----------------
def test_sub_degree_change_crosses_no_threshold_emits_nothing():
    """
    A sub-degree change that crosses no whole-degree tier emits nothing.

    Neither the CC-5 tiers nor the CC-27 hysteresis boundary are crossed, and
    the fan is already on at the right speed and direction, so no command is due.
    """
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.tower",
                    is_on=True,
                    preset_mode="medium",
                    percentage=50,
                    preset_modes=("low", "medium", "high"),
                ),
            ),
            room_temp=76.9,  # int(76.9) == 76: still medium tier, still > 72+0.2
        )
    )
    assert cmds == []


def test_whole_degree_change_crosses_medium_threshold():
    """Crossing from low into medium (CC-5) re-commands the fan's speed."""
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.tower",
                    is_on=True,
                    preset_mode="low",
                    percentage=10,
                    preset_modes=("low", "medium", "high"),
                ),
            ),
            room_temp=75.0,  # int(75) == 75: crosses into medium (>= 75)
        )
    )
    assert _types(cmds) == ["FanSetPreset"]
    assert isinstance(cmds[0], FanSetPreset) and cmds[0].preset_mode == "medium"


def test_whole_degree_change_crosses_high_threshold():
    """Crossing from medium into high (CC-5) re-commands the fan's speed."""
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.tower",
                    is_on=True,
                    preset_mode="medium",
                    percentage=50,
                    preset_modes=("low", "medium", "high"),
                ),
            ),
            room_temp=78.0,  # int(78) == 78: crosses into high (>= 78)
        )
    )
    assert _types(cmds) == ["FanSetPreset"]
    assert isinstance(cmds[0], FanSetPreset) and cmds[0].preset_mode == "high"


def test_companion_fan_percentage():
    cmds = compute_commands(
        _base(
            ac=_climate(fan_modes=()),
            ac_fan=FanInfo(
                "fan.companion",
                is_on=False,
                preset_mode=None,
                percentage=0,
                preset_modes=(),
            ),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert any(isinstance(c, FanSetPercentage) and c.percentage == 100 for c in cmds)


def test_standalone_fan_stepped_speed_grid_no_churn():
    """
    CC-6: a stepped fan already on the grid step for ``low`` isn't re-commanded.

    Reproduces fan.air_circulator (issue #19): percentage_step=11.111 (9
    speeds) snaps a commanded 10% up to speed 1 (11%), which the device then
    reports. The raw-percentage de-dup never matches 11 != 10 and re-sends
    FanSetPercentage every evaluation; the grid-index compare must.
    """
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.air_circulator",
                    is_on=True,
                    percentage=11,
                    percentage_step=11.111111111111111,
                ),
            ),
            room_temp=74.0,
        )
    )
    assert not any(isinstance(c, FanSetPercentage) for c in cmds)


def test_companion_fan_stepped_speed_grid_no_churn():
    """CC-6: same grid-index fix for a companion fan (fan.ceiling_fan, issue #19)."""
    cmds = compute_commands(
        _base(
            ac=_climate(fan_modes=()),
            ac_fan=FanInfo(
                "fan.ceiling_fan",
                is_on=True,
                preset_mode=None,
                percentage=16,
                preset_modes=(),
                percentage_step=8.333333333333334,
            ),
            use_ac=True,
            room_temp=74.0,
        )
    )
    assert not any(isinstance(c, FanSetPercentage) for c in cmds)


def test_standalone_fan_stepped_speed_grid_still_commands_from_off():
    """CC-6: a stepped fan reporting 0% still gets commanded to the raw tier."""
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.air_circulator",
                    is_on=True,
                    percentage=0,
                    percentage_step=11.111111111111111,
                ),
            ),
            room_temp=74.0,
        )
    )
    assert any(isinstance(c, FanSetPercentage) and c.percentage == 10 for c in cmds)


def test_standalone_fan_stepped_speed_grid_tier_change_still_commands():
    """CC-6: a stepped fan on the ``low`` grid step still commands a tier change."""
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.air_circulator",
                    is_on=True,
                    percentage=11,
                    percentage_step=11.111111111111111,
                ),
            ),
            room_temp=79.0,
        )
    )
    assert any(isinstance(c, FanSetPercentage) and c.percentage == 100 for c in cmds)


def test_no_redundant_fan_mode():
    cmds = compute_commands(
        _base(
            ac=_climate(
                hvac="cool", fan_mode="high", fan_modes=("low", "medium", "high")
            ),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert not any(isinstance(c, SetFanMode) for c in cmds)


def test_combined_no_redundant_fan_mode():
    """
    Combined heat pump already in the right mode/setpoint/fan emits no command (CC-19).

    A fractional room_temp change well inside the hysteresis band (CC-27) leaves
    the decision unchanged, so it must not re-issue any device command (which the
    device hears as a beep).
    """
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="heat",
                fan_mode="high",
                hvac_modes=("off", "cool", "heat"),
                fan_modes=("low", "high"),
                current_setpoint=68.0,
            ),
            use_ac=True,
            use_heater=True,
            room_temp=60.4,  # well below 67.8 -> still heating
            target_heating=68.0,
            heating_medium=65.0,
            heating_high=62.0,  # heating_speed(60, 65, 62) -> "high"
        )
    )
    assert cmds == []


def test_split_heater_heats():
    """Heater-alone (no AC, not combined) drives to HEAT with the heating target."""
    cmds = compute_commands(
        _base(
            heater=_climate(
                hvac="off", hvac_modes=("off", "heat"), fan_modes=("low", "high")
            ),
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in cmds)
    assert any(isinstance(c, SetTemperature) and c.temperature == 68 for c in cmds)


def test_combined_off_when_uses_disabled():
    """Combined device with neither use selected turns the climate off, no setpoint."""
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="heat",
                hvac_modes=("off", "cool", "heat"),
                fan_modes=("low", "high"),
            ),
            use_ac=False,
            use_heater=False,
            room_temp=60.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds)
    assert not any(isinstance(c, SetTemperature) for c in cmds)


def test_set_temperature_skipped_when_setpoint_already_correct():
    """SetTemperature skipped when device already has the target setpoint (CC-19)."""
    # Split A/C: setpoint already at the clamped min_temp (62 + 1° margin = 63,
    # per CC-9 — the controller's send-time clamp never actually reports 62),
    # no SetTemperature needed.
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="cool", fan_modes=("low", "high"), current_setpoint=63.0),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert not any(isinstance(c, SetTemperature) for c in cmds)

    # Split A/C: setpoint differs, SetTemperature must be sent.
    cmds_wrong = compute_commands(
        _base(
            ac=_climate(hvac="cool", fan_modes=("low", "high"), current_setpoint=70.0),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert any(isinstance(c, SetTemperature) for c in cmds_wrong)

    # Split heater: setpoint already correct, no SetTemperature needed.
    cmds_heat = compute_commands(
        _base(
            heater=_climate(
                hvac="heat", hvac_modes=("off", "heat"), current_setpoint=68.0
            ),
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
        )
    )
    assert not any(isinstance(c, SetTemperature) for c in cmds_heat)

    # Combined heat pump: setpoint already correct, no SetTemperature needed.
    cmds_combined = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="heat",
                hvac_modes=("off", "cool", "heat"),
                current_setpoint=68.0,
            ),
            use_ac=True,
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
        )
    )
    assert not any(isinstance(c, SetTemperature) for c in cmds_combined)


def test_split_ac_setpoint_idempotent_at_clamped_value():
    """
    CC-9: dedup compares against the *clamped* target, not the raw one.

    min_temp=65 means the engine's raw target is 65, but the controller's
    send-time clamp (CC-9's 1° margin) means the device will actually report
    66. Without comparing against the clamped value, this loops forever
    (issue #28).
    """
    cmds = compute_commands(
        _base(
            ac=_climate(
                hvac="cool",
                fan_modes=("low", "high"),
                min_temp=65.0,
                current_setpoint=66.0,
            ),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert not any(isinstance(c, SetTemperature) for c in cmds)


def test_split_ac_setpoint_still_emitted_when_genuinely_off():
    """A setpoint that doesn't match the clamped target still gets corrected."""
    cmds = compute_commands(
        _base(
            ac=_climate(
                hvac="cool",
                fan_modes=("low", "high"),
                min_temp=65.0,
                current_setpoint=70.0,
            ),
            use_ac=True,
            room_temp=80.0,
        )
    )
    temp_cmds = [c for c in cmds if isinstance(c, SetTemperature)]
    assert len(temp_cmds) == 1
    assert temp_cmds[0].temperature == 65


def test_split_heater_setpoint_idempotent_at_clamped_value():
    """CC-9: same idempotency fix as the split A/C case, for the heater branch."""
    cmds = compute_commands(
        _base(
            heater=_climate(
                hvac="heat",
                hvac_modes=("off", "heat"),
                min_temp=65.0,
                current_setpoint=66.0,
            ),
            use_heater=True,
            room_temp=60.0,
            target_heating=65.0,
        )
    )
    assert not any(isinstance(c, SetTemperature) for c in cmds)


def test_combined_setpoint_idempotent_at_clamped_value():
    """CC-9: same idempotency fix as the split A/C case, for the combined branch."""
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="heat",
                hvac_modes=("off", "cool", "heat"),
                min_temp=65.0,
                current_setpoint=66.0,
            ),
            use_ac=True,
            use_heater=True,
            room_temp=60.0,
            target_heating=65.0,
        )
    )
    assert not any(isinstance(c, SetTemperature) for c in cmds)


def test_set_temperature_sent_when_setpoint_unknown_and_entering():
    """An unknown setpoint sends SetTemperature on a mode transition (off -> cool)."""
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high"), current_setpoint=None),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert any(isinstance(c, SetTemperature) for c in cmds)


def test_set_temperature_resent_when_setpoint_unknown_and_steady():
    """
    CC-19/CC-23 (issue #31 follow-up).

    A device that never reports its setpoint can't be confirmed to have
    converged, so the engine (re)sends SetTemperature every evaluation —
    mirroring CC-23's "unknown never matches" convention for fan direction.
    The controller logs that the device is non-reporting so this expected
    spam is distinguishable from a genuine setpoint mismatch.
    """
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="cool", fan_modes=("low", "high"), current_setpoint=None),
            use_ac=True,
            room_temp=80.0,
        )
    )
    assert any(isinstance(c, SetTemperature) for c in cmds)


def test_split_heater_setpoint_resent_when_unknown_and_steady():
    """Same always-resend behavior as the split A/C case, for the heater."""
    cmds = compute_commands(
        _base(
            heater=_climate(
                hvac="heat", hvac_modes=("off", "heat"), current_setpoint=None
            ),
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
        )
    )
    assert any(isinstance(c, SetTemperature) for c in cmds)


def test_combined_setpoint_resent_when_unknown_and_steady():
    """Same always-resend behavior as the split A/C case, for combined mode."""
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="heat",
                hvac_modes=("off", "cool", "heat"),
                current_setpoint=None,
            ),
            use_ac=True,
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
        )
    )
    assert any(isinstance(c, SetTemperature) for c in cmds)


def test_split_ac_idle_restarts_at_next_degree():
    """CC-27: an off A/C does not restart cooling until the room reaches target + 1°."""
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high")),
            use_ac=True,
            room_temp=72.9,  # below the 73.0 restart threshold -> stays off
            target_cooling=72.0,
        )
    )
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds)

    # At the next whole degree (73) the room crosses the restart threshold.
    cmds_hot = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high")),
            use_ac=True,
            room_temp=73.0,
            target_cooling=72.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds_hot)


# --- CC-27 hysteresis deadband ---------------------------------------------
def test_split_ac_hysteresis_keeps_cooling_near_target():
    """CC-27: a running A/C keeps cooling until the room is within 0.2° of target."""
    # Running (hvac=cool), room 71.5, target 71 -> 71.5 > 71.2, still cooling.
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="cool", fan_modes=("low", "high"), current_setpoint=62.0),
            use_ac=True,
            room_temp=71.5,
            target_cooling=71.0,
        )
    )
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds)

    # Running, room 71.1 -> within 0.2° of target, cooling turns off.
    cmds_off = compute_commands(
        _base(
            ac=_climate(hvac="cool", fan_modes=("low", "high"), current_setpoint=62.0),
            use_ac=True,
            room_temp=71.1,
            target_cooling=71.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds_off)


def test_split_heater_hysteresis():
    """CC-27: heater holds until within 0.2° of target, restarts a degree below."""
    # Running heat, room 67.5, target 68 -> 67.5 < 67.8, still heating.
    cmds = compute_commands(
        _base(
            heater=_climate(
                hvac="heat", hvac_modes=("off", "heat"), current_setpoint=68.0
            ),
            use_heater=True,
            room_temp=67.5,
            target_heating=68.0,
        )
    )
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds)

    # Running heat, room 67.9 -> within 0.2° of target, turns off.
    cmds_off = compute_commands(
        _base(
            heater=_climate(
                hvac="heat", hvac_modes=("off", "heat"), current_setpoint=68.0
            ),
            use_heater=True,
            room_temp=67.9,
            target_heating=68.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds_off)

    # Off, room 67.5 -> above the 67.0 restart threshold, stays off.
    cmds_idle = compute_commands(
        _base(
            heater=_climate(hvac="off", hvac_modes=("off", "heat")),
            use_heater=True,
            room_temp=67.5,
            target_heating=68.0,
        )
    )
    assert not any(
        isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in cmds_idle
    )

    # Off, room 67.0 (target - 1°) -> heating restarts.
    cmds_cold = compute_commands(
        _base(
            heater=_climate(hvac="off", hvac_modes=("off", "heat")),
            use_heater=True,
            room_temp=67.0,
            target_heating=68.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in cmds_cold)


def test_combined_hysteresis():
    """CC-27: a running combined heat pump keeps cooling near target; idle holds."""
    # Running cool, room 71.5, target 71 -> still cooling (no off).
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="cool",
                hvac_modes=("off", "cool", "heat"),
                current_setpoint=62.0,
            ),
            use_ac=True,
            use_heater=True,
            room_temp=71.5,
            target_cooling=71.0,
            target_heating=68.0,
        )
    )
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds)

    # Off, room 71.5 -> below the 72.0 cooling restart threshold and above the
    # heating restart threshold, so the device stays off.
    cmds_idle = compute_commands(
        _base(
            combined=True,
            ac=_climate(hvac="off", hvac_modes=("off", "cool", "heat")),
            use_ac=True,
            use_heater=True,
            room_temp=71.5,
            target_cooling=71.0,
            target_heating=68.0,
        )
    )
    assert not any(
        isinstance(c, SetHvacMode) and c.hvac_mode in ("cool", "heat")
        for c in cmds_idle
    )


def test_standalone_fan_hysteresis():
    """CC-27: a running standalone fan holds until within 0.2° of target."""

    def fan(is_on):
        return _fan_control(
            "fan.tower",
            is_on=is_on,
            preset_mode="low" if is_on else None,
            percentage=10 if is_on else 0,
            preset_modes=("low", "medium", "high"),
        )

    # Running, room 72.5, target 72 -> 72.5 > 72.2, stays on (no turn-off).
    cmds = compute_commands(_base(fans=(fan(is_on=True),), room_temp=72.5))
    assert not any(type(c).__name__ == "FanTurnOff" for c in cmds)

    # Running, room 72.1 -> within 0.2° of target, fan turns off.
    cmds_off = compute_commands(_base(fans=(fan(is_on=True),), room_temp=72.1))
    assert any(type(c).__name__ == "FanTurnOff" for c in cmds_off)

    # Off, room 72.5 -> below the 73.0 restart threshold, stays off.
    cmds_idle = compute_commands(_base(fans=(fan(is_on=False),), room_temp=72.5))
    assert not any(type(c).__name__ == "FanTurnOn" for c in cmds_idle)

    # Off, room 73.0 (target + 1°) -> fan restarts.
    cmds_on = compute_commands(_base(fans=(fan(is_on=False),), room_temp=73.0))
    assert any(type(c).__name__ == "FanTurnOn" for c in cmds_on)


def test_combined_fan_only_uses_cooling_tiers():
    """Combined FAN_ONLY with AC in use picks cooling fan tiers."""
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="cool",
                hvac_modes=("off", "cool", "heat", "fan_only"),
                fan_modes=("low", "high"),
            ),
            use_ac=True,
            use_heater=True,
            room_temp=72.0,  # within deadband: no cool, no heat
            target_cooling=75.0,
            target_heating=68.0,
            cooling_medium=75.0,
            cooling_high=78.0,
            ac_fan_only_override=True,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "fan_only" for c in cmds)
    # cooling_speed(72, 75, 78) -> "low"
    assert any(isinstance(c, SetFanMode) and c.fan_mode == "low" for c in cmds)


def test_combined_fan_only_heater_only_uses_heating_tiers():
    """
    Combined FAN_ONLY with only the heater in use picks heating fan tiers.

    Chosen so heating and cooling tiers diverge: heating_speed(63, 65, 62) -> "medium"
    while cooling_speed(63, 75, 78) -> "low", proving the heating path is taken.
    """
    cmds = compute_commands(
        _base(
            combined=True,
            ac=_climate(
                hvac="heat",
                hvac_modes=("off", "cool", "heat", "fan_only"),
                fan_modes=("low", "medium", "high"),
            ),
            use_ac=False,
            use_heater=True,
            room_temp=63.0,  # >= target_heating, so no active heating
            target_heating=62.0,
            heating_medium=65.0,
            heating_high=62.0,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "fan_only" for c in cmds)
    assert any(isinstance(c, SetFanMode) and c.fan_mode == "medium" for c in cmds)


# --- window sensor (CC-20 / CC-21) ------------------------------------------
def test_window_open_blocks_split_ac():
    """CC-20: open window suppresses Cool regardless of temp delta / Use toggle."""
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high")),
            use_ac=True,
            room_temp=80.0,
            window_open=True,
        )
    )
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds)
    assert not any(isinstance(c, SetTemperature) for c in cmds)


def test_window_open_turns_off_running_ac():
    """CC-20: an A/C actively cooling is turned off when the window opens."""
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="cool", fan_modes=("low", "high")),
            use_ac=True,
            room_temp=80.0,
            window_open=True,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds)
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds)


def test_window_open_turns_off_running_heater():
    """CC-20: a (non-fan) heater actively heating is turned off on window open."""
    cmds = compute_commands(
        _base(
            heater=_climate(hvac="heat", hvac_modes=("off", "heat")),
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
            window_open=True,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "off" for c in cmds)
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in cmds)


def test_window_open_combined_blocks_both():
    """CC-20: a combined heat pump conditions in neither direction when open."""
    cooling = compute_commands(
        _base(
            combined=True,
            ac=_climate(hvac="cool", hvac_modes=("off", "cool", "heat")),
            use_ac=True,
            room_temp=80.0,
            window_open=True,
        )
    )
    assert not any(
        isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cooling
    )

    heating = compute_commands(
        _base(
            combined=True,
            ac=_climate(hvac="heat", hvac_modes=("off", "cool", "heat")),
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
            window_open=True,
        )
    )
    assert not any(
        isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in heating
    )


def test_window_close_resumes_cooling():
    """CC-20: closing the window re-enables Cool (pure re-evaluation)."""
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high")),
            use_ac=True,
            room_temp=80.0,
            window_open=False,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds)
    assert any(isinstance(c, SetTemperature) for c in cmds)


def test_window_open_standalone_fan_unaffected():
    """CC-20: the standalone fan runs identically open or closed."""

    def _run(*, window_open):
        return compute_commands(
            _base(
                fans=(
                    _fan_control("fan.tower", preset_modes=("low", "medium", "high")),
                ),
                room_temp=80.0,
                window_open=window_open,
            )
        )

    assert _types(_run(window_open=True)) == _types(_run(window_open=False))
    assert any(type(c).__name__ == "FanTurnOn" for c in _run(window_open=True))


def test_window_open_targets_written_but_suppressed():
    """CC-20: a just-applied profile's low target still can't cool while open."""
    cmds = compute_commands(
        _base(
            ac=_climate(hvac="off", fan_modes=("low", "high")),
            use_ac=True,
            room_temp=80.0,
            target_cooling=68.0,  # freshly applied aggressive cooling target
            window_open=True,
        )
    )
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds)


def test_window_open_ac_fan_only_override_still_runs():
    """CC-20: fan-only override is circulation, not conditioning — still runs."""
    cmds = compute_commands(
        _base(
            ac=_climate(
                hvac_modes=("off", "cool", "fan_only"), fan_modes=("low", "high")
            ),
            use_ac=True,
            room_temp=80.0,
            ac_fan_only_override=True,
            window_open=True,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "fan_only" for c in cmds)
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "cool" for c in cmds)


def test_window_open_heater_native_fan_only():
    """CC-20: a fan-capable heater still runs native fan-only with the window open."""
    cmds = compute_commands(
        _base(
            heater=_climate(
                hvac="heat",
                hvac_modes=("off", "heat", "fan_only"),
                fan_modes=("low", "high"),
            ),
            use_heater=True,
            room_temp=60.0,
            target_heating=68.0,
            window_open=True,
        )
    )
    assert any(isinstance(c, SetHvacMode) and c.hvac_mode == "fan_only" for c in cmds)
    assert not any(isinstance(c, SetHvacMode) and c.hvac_mode == "heat" for c in cmds)


# --- multi-window aggregation + fail-safe (CC-20 / CC-21) -------------------
def test_no_window_sensors_is_closed():
    """CC-21: a room with no window sensors is never "open"."""
    assert any_window_open(()) is False


def test_single_window_open_and_closed():
    """CC-20: one sensor — open when "on", closed otherwise."""
    assert any_window_open(("on",)) is True
    assert any_window_open(("off",)) is False


def test_two_windows_aggregate_any_open():
    """CC-20: with two sensors the room is open if EITHER reads "on"."""
    assert any_window_open(("off", "off")) is False  # both closed
    assert any_window_open(("on", "on")) is True  # both open
    assert any_window_open(("off", "on")) is True  # one open
    assert any_window_open(("on", "off")) is True  # one open (order-independent)


def test_window_failsafe_bad_states_are_closed():
    """CC-21: missing/unavailable/unknown readings are treated as closed."""
    assert any_window_open((None,)) is False
    assert any_window_open(("unavailable", "unknown")) is False
    # A good "on" still wins even when another sensor is unavailable.
    assert any_window_open(("unavailable", "on")) is True


# --- multiple standalone fans per room (CC-13 / CC-14) ----------------------
def test_two_fans_different_targets_only_one_runs():
    """
    CC-13: two fans with distinct targets — only the one past its threshold runs.

    room_temp=74 is a full degree past fan A's target (72) so it starts, but
    below fan B's restart threshold (target 76 -> restarts at 77), so B stays off.
    Commands must address the correct distinct entity_ids.
    """
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.a",
                    target=72.0,
                    medium=75.0,
                    high=78.0,
                    preset_modes=("low", "medium", "high"),
                ),
                _fan_control(
                    "fan.b",
                    target=76.0,
                    medium=79.0,
                    high=82.0,
                    preset_modes=("low", "medium", "high"),
                ),
            ),
            room_temp=74.0,
        )
    )
    on = [c for c in cmds if isinstance(c, FanTurnOn)]
    assert [c.entity_id for c in on] == ["fan.a"]
    # cooling_speed(74, 75, 78) -> "low" for fan.a
    presets = [c for c in cmds if isinstance(c, FanSetPreset)]
    assert [(c.entity_id, c.preset_mode) for c in presets] == [("fan.a", "low")]
    assert not any(
        isinstance(c, (FanTurnOn, FanSetPreset, FanSetPercentage))
        and c.entity_id == "fan.b"
        for c in cmds
    )


def test_shared_offsets_per_fan_yield_different_speed_tiers():
    """
    CC-14: shared offsets per fan give different tiers at the same room_temp.

    Different absolute thresholds come from a shared offset over distinct targets.
    Both fans share medium/high offsets of +3/+6, but different targets shift the
    thresholds. At room_temp=79 (both already running): fan A (target 72 ->
    medium 75 / high 78) is High; fan B (target 76 -> medium 79 / high 82) is
    Medium.
    """
    cmds = compute_commands(
        _base(
            fans=(
                _fan_control(
                    "fan.a",
                    is_on=True,
                    percentage=10,
                    target=72.0,
                    medium=75.0,
                    high=78.0,
                    preset_modes=("low", "medium", "high"),
                ),
                _fan_control(
                    "fan.b",
                    is_on=True,
                    percentage=10,
                    target=76.0,
                    medium=79.0,
                    high=82.0,
                    preset_modes=("low", "medium", "high"),
                ),
            ),
            room_temp=79.0,
        )
    )
    presets = {c.entity_id: c.preset_mode for c in cmds if isinstance(c, FanSetPreset)}
    assert presets == {"fan.a": "high", "fan.b": "medium"}


def test_per_fan_reverse_each_gets_its_own_direction():
    """
    CC-22: two reversible running fans each get their own direction command.

    One reverse=True, one reverse=False — each gets its own FanSetDirection with
    its own requested direction, and a fan already at the requested one gets none.
    """
    cmds = compute_commands(
        _base(
            fans=(
                # Wants reverse, currently forward -> emits reverse.
                _fan_control(
                    "fan.rev",
                    is_on=True,
                    percentage=10,
                    reverse=True,
                    reversible=True,
                    direction="forward",
                    preset_modes=("low", "medium", "high"),
                    preset_mode="low",
                ),
                # Wants forward, already forward -> emits nothing for direction.
                _fan_control(
                    "fan.fwd",
                    is_on=True,
                    percentage=10,
                    reverse=False,
                    reversible=True,
                    direction="forward",
                    preset_modes=("low", "medium", "high"),
                    preset_mode="low",
                ),
            ),
            room_temp=74.0,
        )
    )
    dirs = [c for c in cmds if isinstance(c, FanSetDirection)]
    assert [(c.entity_id, c.direction) for c in dirs] == [("fan.rev", "reverse")]


def test_fans_are_independent_turning_one_use_off():
    """CC-13: turning one fan's Use off turns only that fan off; the other runs."""
    cmds = compute_commands(
        _base(
            fans=(
                # Use off but currently on -> turned off.
                _fan_control(
                    "fan.off",
                    use=False,
                    is_on=True,
                    percentage=50,
                    preset_mode="medium",
                    preset_modes=("low", "medium", "high"),
                ),
                # Use on and past threshold -> keeps running / commanded.
                _fan_control(
                    "fan.on",
                    use=True,
                    is_on=True,
                    percentage=10,
                    preset_mode="low",
                    preset_modes=("low", "medium", "high"),
                ),
            ),
            room_temp=76.0,
        )
    )
    offs = [c for c in cmds if isinstance(c, FanTurnOff)]
    assert [c.entity_id for c in offs] == ["fan.off"]
    # fan.on stays running; cooling_speed(76, 75, 78) -> "medium".
    presets = [c for c in cmds if isinstance(c, FanSetPreset)]
    assert [(c.entity_id, c.preset_mode) for c in presets] == [("fan.on", "medium")]
    assert not any(
        isinstance(c, (FanTurnOn, FanSetPreset, FanSetPercentage))
        and c.entity_id == "fan.off"
        for c in cmds
    )
