"""
Tests for standalone ceiling fan direction (reverse / forward) control.

Scope: the room's **standalone fan only** — the AC/heater companion fans do not
get direction control (decided in the feature design; see
requirements/spec/climate-control.md CC-22..CC-25).

Behavior decisions pinned here:

- Reversibility is auto-detected (``FanInfo.reversible`` mirrors the HA fan
  entity's DIRECTION capability); the engine never emits ``FanSetDirection``
  for a non-reversible fan (CC-24).
- Strict idempotence (CC-23): ``FanSetDirection`` is emitted only when the
  fan's reported ``direction`` differs from the request — an unknown (``None``)
  direction never matches, so it emits.
- Direction is applied only while the fan is actively running (CC-25); a
  reverse request while the fan is off takes effect at the next turn-on, with
  ``FanTurnOn`` ordered before ``FanSetDirection``.

Like ``test_engine.py``, this loads the pure modules via the ``_load`` shim and
runs under plain ``pytest`` — no HA test harness required.
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


_load("fan_logic")
_load("engine")

from rc_pure.engine import (  # noqa: E402
    EngineInputs,
    FanControl,
    FanInfo,
    FanSetDirection,
    compute_commands,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rfan(
    entity_id="fan.ceiling",
    *,
    is_on=True,
    direction="forward",
    preset_modes=("low", "medium", "high"),
):
    """Build a reversible FanInfo (native DIRECTION feature) at the given direction."""
    return FanInfo(
        entity_id,
        is_on=is_on,
        preset_mode="medium" if is_on else None,
        percentage=50 if is_on else 0,
        preset_modes=tuple(preset_modes),
        reversible=True,
        direction=direction,
    )


def _rfan_preset(
    entity_id="fan.ceiling",
    *,
    is_on=True,
    current_direction="forward",
    forward_preset="normal",
):
    """Build a FanInfo where direction is via preset_mode (CC-22 preset path)."""
    preset_mode = "reverse" if current_direction == "reverse" else forward_preset
    return FanInfo(
        entity_id,
        is_on=is_on,
        preset_mode=preset_mode if is_on else None,
        percentage=50 if is_on else 0,
        preset_modes=("normal", "natural", "sleep", "reverse"),
        reversible=True,
        direction=current_direction,
        direction_via_preset=True,
        forward_preset=forward_preset,
    )


def _fans(info, *, use=True, target=72.0, medium=75.0, high=78.0, reverse=False):
    """
    Wrap a single FanInfo in the one-element ``fans`` tuple the engine expects.

    Defaults mirror the old singular ``target_fan=72``/``fan_medium=75``/
    ``fan_high=78`` config so the "fan runs above target" intent is preserved.
    """
    return (
        FanControl(
            info=info,
            use=use,
            target=target,
            medium=medium,
            high=high,
            reverse=reverse,
        ),
    )


def _base(**kw):
    defaults = dict(
        combined=False,
        # room_temp=76 keeps the standalone fan above target 72 so it runs
        room_temp=76.0,
        ac=None,
        heater=None,
        fans=(),
        ac_fan=None,
        heater_fan=None,
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


# ---------------------------------------------------------------------------
# CC-22: direction command emitted when desired != current
# ---------------------------------------------------------------------------


def test_reverse_emits_direction_command():
    """CC-22: running fan currently 'forward' gets FanSetDirection('reverse')."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(direction="forward"), reverse=True),
        )
    )
    assert any(
        isinstance(c, FanSetDirection)
        and c.entity_id == "fan.ceiling"
        and c.direction == "reverse"
        for c in cmds
    )


def test_forward_emits_direction_command():
    """CC-22: running fan currently 'reverse' gets FanSetDirection('forward')."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(direction="reverse"), reverse=False),
        )
    )
    assert any(
        isinstance(c, FanSetDirection)
        and c.entity_id == "fan.ceiling"
        and c.direction == "forward"
        for c in cmds
    )


def test_unknown_direction_emits_command():
    """CC-23: an unknown reported direction never matches, so the command is sent."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(direction=None), reverse=False),
        )
    )
    assert any(
        isinstance(c, FanSetDirection) and c.direction == "forward" for c in cmds
    )


# ---------------------------------------------------------------------------
# CC-23: idempotence — no direction command when already correct
# ---------------------------------------------------------------------------


def test_no_direction_command_when_already_reversed():
    """CC-23: no FanSetDirection when the fan is already spinning in reverse."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(direction="reverse"), reverse=True),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


def test_no_direction_command_when_already_forward():
    """CC-23: no FanSetDirection when the fan is already spinning forward."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(direction="forward"), reverse=False),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


# ---------------------------------------------------------------------------
# CC-24: non-reversible fans never receive direction commands
# ---------------------------------------------------------------------------


def test_non_reversible_fan_no_direction_command():
    """CC-24: a non-reversible fan never gets FanSetDirection on reverse."""
    cmds = compute_commands(
        _base(
            fans=_fans(
                FanInfo(
                    "fan.ceiling",
                    is_on=True,
                    preset_mode="medium",
                    percentage=50,
                    preset_modes=("low", "medium", "high"),
                ),
                reverse=True,
            ),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


def test_non_reversible_fan_no_direction_command_when_forward():
    """CC-24: forward request on a non-reversible fan also emits nothing."""
    cmds = compute_commands(
        _base(
            fans=_fans(
                FanInfo(
                    "fan.ceiling",
                    is_on=True,
                    preset_mode="low",
                    percentage=10,
                    preset_modes=("low", "medium", "high"),
                ),
                reverse=False,
            ),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


# ---------------------------------------------------------------------------
# CC-25: direction only applied when fan is actively running
# ---------------------------------------------------------------------------


def test_no_direction_command_when_fan_off_below_threshold():
    """CC-25: no FanSetDirection when room is below fan threshold so fan stays off."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(is_on=False, direction="forward"), use=True, reverse=True),
            room_temp=70.0,  # below fan target 72 → engine should not run the fan
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


def test_no_direction_command_when_use_fan_off():
    """CC-25: no FanSetDirection when use_fan=False (fan is being turned off)."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(is_on=True, direction="forward"), use=False, reverse=True),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


def test_direction_command_ordering():
    """CC-25: FanTurnOn precedes FanSetDirection when the fan must start and reverse."""
    # Fan is currently off but should be on (room_temp > target_fan) and needs reversal.
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan(is_on=False, direction="forward"), use=True, reverse=True),
            room_temp=76.0,  # above target → fan should turn on
        )
    )
    names = [type(c).__name__ for c in cmds]
    assert "FanTurnOn" in names
    assert "FanSetDirection" in names
    assert names.index("FanTurnOn") < names.index("FanSetDirection")


# ---------------------------------------------------------------------------
# CC-22 preset path: direction expressed as preset_mode (e.g. Dreo ceiling fans)
# ---------------------------------------------------------------------------


def test_via_preset_reverse_sets_via_preset_flag():
    """CC-22: FanSetDirection carries via_preset=True for preset-direction fans."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan_preset(current_direction="forward"), reverse=True),
        )
    )
    dir_cmds = [c for c in cmds if isinstance(c, FanSetDirection)]
    assert len(dir_cmds) == 1
    assert dir_cmds[0].direction == "reverse"
    assert dir_cmds[0].via_preset is True


def test_via_preset_forward_carries_forward_preset():
    """CC-22: forward FanSetDirection on preset fan carries the forward_preset name."""
    cmds = compute_commands(
        _base(
            fans=_fans(
                _rfan_preset(current_direction="reverse", forward_preset="normal"),
                reverse=False,
            ),
        )
    )
    dir_cmds = [c for c in cmds if isinstance(c, FanSetDirection)]
    assert len(dir_cmds) == 1
    assert dir_cmds[0].direction == "forward"
    assert dir_cmds[0].via_preset is True
    assert dir_cmds[0].forward_preset == "normal"


def test_via_preset_idempotent_when_already_reverse():
    """CC-23: no FanSetDirection when preset fan is already in reverse."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan_preset(current_direction="reverse"), reverse=True),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


def test_via_preset_idempotent_when_already_forward():
    """CC-23: no FanSetDirection when preset fan is already forward."""
    cmds = compute_commands(
        _base(
            fans=_fans(_rfan_preset(current_direction="forward"), reverse=False),
        )
    )
    assert not any(isinstance(c, FanSetDirection) for c in cmds)


def test_via_preset_turn_on_precedes_direction():
    """CC-25: FanTurnOn precedes FanSetDirection on the preset path too."""
    cmds = compute_commands(
        _base(
            fans=_fans(
                _rfan_preset(is_on=False, current_direction="forward"),
                use=True,
                reverse=True,
            ),
            room_temp=76.0,
        )
    )
    names = [type(c).__name__ for c in cmds]
    assert "FanTurnOn" in names
    assert "FanSetDirection" in names
    assert names.index("FanTurnOn") < names.index("FanSetDirection")
