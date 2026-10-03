"""
Tests for the vent fan manual override (CC-38/CC-39/CC-40, CC-L11).

Same approach as ``test_vent_fan_control.py``: ``controller.py`` imports
directly (Home Assistant is installed in this devcontainer), ``_resolve`` is
monkeypatched to bypass the entity registry, and ``hass`` is a tiny stand-in.
No live event loop: ``async_call_later`` is replaced with a recorder, and
``_publish_vent_override`` / ``async_request_run`` are patched per test.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import Context

from custom_components.btoddb_room_climate_controller import controller as ctrl_mod
from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.const import (
    KEY_MANUAL_MODE,
    SIGNAL_VENT_OVERRIDE,
    VENT_ECHO_WINDOW_SECONDS,
    VENT_OVERRIDE_SECONDS,
)
from custom_components.btoddb_room_climate_controller.controller import (
    RoomController,
    _classify_vent_change,
)
from custom_components.btoddb_room_climate_controller.engine import (
    EngineInputs,
    SwitchTurnOn,
    VentFanControl,
)

VENT = "switch.office_vent"
MANUAL_MODE = f"switch.{KEY_MANUAL_MODE}"


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": False,
        "has_heater": False,
        "has_fan": False,
        "combined": False,
        "ac_climate": None,
        "heater_climate": None,
        "fan_entities": (),
        "ac_fan_entity": None,
        "heater_fan_entity": None,
        "ac_power_switch": None,
        "heater_power_switch": None,
        "temperature_sensor": "sensor.office_temp",
        "humidity_sensor": None,
        "power_sensor": None,
        "window_sensors": (),
        "ac_fan_only": False,
        "heater_fan_only": False,
        "limits": {
            "cooling": {"min": 60.0, "max": 90.0},
            "heating": {"min": 50.0, "max": 80.0},
            "fan": {"min": 60.0, "max": 90.0},
            "vent_fan": {"min": 60.0, "max": 86.0},
        },
        "command_delay": 1.0,
        "power_on_delay": 2.0,
        "heater_setpoint_offset": 2,
        "ac_setpoint_offset": 2,
        "has_vent_fan": True,
        "vent_fan_entity": VENT,
    }
    defaults.update(overrides)
    return models.Room(**defaults)


class _StubState:
    def __init__(self, value):
        self.state = str(value)
        self.attributes = {}


class _StubStates:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        return None if value is None else _StubState(value)


class _StubServices:
    """Records calls with their kwargs; can simulate a failing service."""

    def __init__(self, *, fail=False):
        self.calls: list[tuple[str, str, dict, dict]] = []
        self._fail = fail

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, data, kwargs))
        if self._fail:
            msg = f"simulated failure: {domain}.{service}"
            raise RuntimeError(msg)


class _StubHass:
    def __init__(self, values, *, fail=False):
        self.states = _StubStates(values)
        self.services = _StubServices(fail=fail)


class _FakeEvent:
    """Minimal ``Event`` stand-in: ``data`` plus a real ``Context``."""

    def __init__(self, data, context):
        self.data = data
        self.context = context


def _event(old, new, context=None):
    return _FakeEvent(
        {
            "entity_id": VENT,
            "old_state": None if old is None else _StubState(old),
            "new_state": None if new is None else _StubState(new),
        },
        context or Context(),
    )


def _make_controller(values=None, *, fail=False):
    values = {MANUAL_MODE: "off", VENT: "off"} if values is None else values
    hass = _StubHass(values, fail=fail)
    entry = MagicMock()
    entry.entry_id = "entry1"
    controller = RoomController(hass, entry, _room())
    controller._resolve = lambda key, domain: f"{domain}.{key}"
    return controller


@pytest.fixture
def timers(monkeypatch):
    """Replace ``async_call_later`` with a recorder of ``(delay, cb, cancel)``."""
    calls: list[tuple[float, object, MagicMock]] = []

    def fake_call_later(_hass, delay, cb):
        cancel = MagicMock()
        calls.append((delay, cb, cancel))
        return cancel

    monkeypatch.setattr(ctrl_mod, "async_call_later", fake_call_later)
    return calls


@pytest.fixture
def hooks():
    """Record ``_publish_vent_override`` and ``async_request_run`` calls."""
    return {"published": [], "runs": []}


def _wire(controller, hooks):
    def publish():
        if controller._vent_override is None:
            state = "none"
        else:
            state = "on" if controller._vent_override else "off"
        hooks["published"].append((state, controller._vent_override_until))

    controller._publish_vent_override = publish
    controller.async_request_run = lambda trigger="evaluation": hooks["runs"].append(
        trigger
    )
    return controller


# -- classification (CC-39) --------------------------------------------------
def _st(value):
    return _StubState(value)


def test_classify_contextless_transition_is_manual():
    """CC-39: an on<->off change with no user and no parent is manual."""
    assert _classify_vent_change(_st("on"), _st("off"), Context(), None, 0.0) == (
        "manual"
    )
    assert _classify_vent_change(_st("off"), _st("on"), Context(), None, 0.0) == (
        "manual"
    )


def test_classify_user_context_is_user():
    """CC-39: a dashboard/service change by a user never starts an override."""
    ctx = Context(user_id="abc")
    assert _classify_vent_change(_st("off"), _st("on"), ctx, None, 0.0) == "user"


def test_classify_parent_context_is_automation():
    """CC-39: a context-carrying automation never starts an override."""
    ctx = Context(parent_id="parent")
    assert _classify_vent_change(_st("off"), _st("on"), ctx, None, 0.0) == "automation"


def test_classify_own_context_id_is_own():
    """CC-39: the controller's own command context is its echo, whatever the state."""
    ctx = Context()
    own = (ctx.id, True, 0.0)
    assert _classify_vent_change(_st("off"), _st("on"), ctx, own, 999.0) == "own"
    assert _classify_vent_change(_st("on"), _st("off"), ctx, own, 999.0) == "own"


def test_classify_own_context_id_wins_over_user_and_parent():
    """CC-39: the own-context check runs before the user/parent checks."""
    ctx = Context(user_id="abc", parent_id="parent")
    own = (ctx.id, True, 0.0)
    assert _classify_vent_change(_st("off"), _st("on"), ctx, own, 999.0) == "own"


def test_classify_commanded_state_within_echo_window_is_own():
    """CC-39: a fresh context arriving in the commanded state within 10 s is own."""
    own = ("other-id", True, 100.0)
    now = 100.0 + VENT_ECHO_WINDOW_SECONDS
    assert _classify_vent_change(_st("off"), _st("on"), Context(), own, now) == "own"


def test_classify_user_within_echo_window_is_user():
    """CC-39: a user toggling to the commanded state within 10 s is still user."""
    own = ("other-id", True, 100.0)
    ctx = Context(user_id="abc")
    assert _classify_vent_change(_st("off"), _st("on"), ctx, own, 101.0) == "user"


def test_classify_automation_within_echo_window_is_automation():
    """CC-39: an automation reaching the commanded state within 10 s is automation."""
    own = ("other-id", True, 100.0)
    ctx = Context(parent_id="parent")
    assert _classify_vent_change(_st("off"), _st("on"), ctx, own, 101.0) == "automation"


def test_classify_commanded_state_after_echo_window_is_manual():
    """CC-39: the same change 11 s after the command is a manual press."""
    own = ("other-id", True, 100.0)
    assert (
        _classify_vent_change(_st("off"), _st("on"), Context(), own, 111.0) == "manual"
    )


def test_classify_opposite_state_within_echo_window_is_manual():
    """CC-39: a fresh context going against the commanded state is manual."""
    own = ("other-id", True, 100.0)
    assert (
        _classify_vent_change(_st("on"), _st("off"), Context(), own, 101.0) == "manual"
    )


def test_classify_non_transitions_are_ignored():
    """CC-39: unavailable/unknown, missing old state and same-state updates."""
    ctx = Context()
    assert _classify_vent_change(_st("unavailable"), _st("on"), ctx, None, 0) == (
        "ignored"
    )
    assert _classify_vent_change(_st("on"), _st("unknown"), ctx, None, 0) == ("ignored")
    assert _classify_vent_change(None, _st("on"), ctx, None, 0) == "ignored"
    assert _classify_vent_change(_st("on"), _st("on"), ctx, None, 0) == "ignored"


# -- controller (CC-38/CC-40) ------------------------------------------------
def test_manual_on_starts_override(timers, hooks):
    """CC-38: a manual ON arms the 20 min timer, publishes, and re-evaluates."""
    controller = _wire(_make_controller(), hooks)

    controller._on_vent_change(_event("off", "on"))

    assert len(timers) == 1
    assert timers[0][0] == VENT_OVERRIDE_SECONDS
    assert len(hooks["published"]) == 1
    state, until = hooks["published"][0]
    assert state == "on"
    assert until is not None
    assert until == controller._vent_override_until
    assert hooks["runs"] == ["vent fan override started"]
    vent = controller._vent_fan_control(controller.room)
    assert vent is not None
    assert vent.override is True


def test_override_expiry_reverts_to_rules(timers, hooks):
    """CC-38: on expiry the override clears and an evaluation runs at once."""
    controller = _wire(_make_controller(), hooks)
    controller._on_vent_change(_event("on", "off"))
    assert controller._vent_override is False

    _delay, callback, _cancel = timers[0]
    callback(None)

    assert controller._vent_override is None
    assert controller._vent_override_until is None
    assert controller._unsub_vent_timer is None
    assert hooks["published"][-1] == ("none", None)
    assert hooks["runs"][-1] == "vent fan override expired"
    vent = controller._vent_fan_control(controller.room)
    assert vent is not None
    assert vent.override is None


def test_second_manual_toggle_restarts_override(timers, hooks):
    """CC-38: a further manual change restarts the 20 min with the new state."""
    controller = _wire(_make_controller(), hooks)
    controller._on_vent_change(_event("off", "on"))
    first_cancel = timers[0][2]

    controller._on_vent_change(_event("on", "off"))

    first_cancel.assert_called_once()
    assert len(timers) == 2
    assert timers[1][0] == VENT_OVERRIDE_SECONDS
    assert controller._vent_override is False
    assert hooks["published"][-1][0] == "off"


def test_no_override_starts_in_manual_mode(timers, hooks, caplog):
    """CC-40: a manual press while Manual Mode is on starts nothing."""
    controller = _wire(_make_controller({MANUAL_MODE: "on", VENT: "on"}), hooks)

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_vent_change(_event("off", "on"))

    assert timers == []
    assert hooks["published"] == []
    assert hooks["runs"] == []
    assert controller._vent_override is None
    assert any("no override (manual mode)" in r.message for r in caplog.records)


def test_manual_mode_clears_active_override(timers, hooks, caplog):
    """CC-40: turning Manual Mode on clears an active override and its timer."""
    controller = _wire(_make_controller(), hooks)
    controller._on_vent_change(_event("off", "on"))
    cancel = timers[0][2]
    controller.hass.states._values[MANUAL_MODE] = "on"

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        assert controller._build_inputs() is None

    cancel.assert_called_once()
    assert controller._vent_override is None
    assert controller._unsub_vent_timer is None
    assert hooks["published"][-1] == ("none", None)
    assert any(
        "Vent fan override cleared: manual mode" in r.message for r in caplog.records
    )


def test_async_stop_cancels_timer_and_vent_listener(timers, hooks):
    """CC-40: stopping (unload/reload) drops the listener and the pending timer."""
    controller = _wire(_make_controller(), hooks)
    controller._on_vent_change(_event("off", "on"))
    cancel = timers[0][2]
    unsub_vent = MagicMock()
    controller._unsub_vent = unsub_vent

    controller.async_stop()

    cancel.assert_called_once()
    unsub_vent.assert_called_once()
    assert controller._unsub_vent is None
    assert controller._unsub_vent_timer is None


@pytest.mark.usefixtures("timers")
def test_async_start_registers_separate_vent_listener(monkeypatch, hooks):
    """CC-38: the vent entity gets its own listener, outside the tracked set."""
    tracked: list[tuple[list[str], object]] = []

    def fake_track(_hass, entity_ids, cb):
        tracked.append((list(entity_ids), cb))
        return MagicMock()

    monkeypatch.setattr(ctrl_mod, "async_track_state_change_event", fake_track)
    controller = _wire(_make_controller(), hooks)

    controller.async_start()

    assert ([VENT], controller._on_vent_change) in tracked
    assert VENT not in controller._tracked
    assert controller._unsub_vent is not None


@pytest.mark.parametrize(
    "context", [Context(user_id="abc"), Context(parent_id="parent")]
)
def test_user_or_automation_change_starts_nothing(timers, hooks, context):
    """CC-39: user/automation changes start no override and request no run."""
    controller = _wire(_make_controller(), hooks)

    controller._on_vent_change(_event("off", "on", context))

    assert timers == []
    assert hooks["published"] == []
    assert hooks["runs"] == []
    assert controller._vent_override is None


@pytest.mark.usefixtures("timers")
def test_classification_is_logged(hooks, caplog):
    """CC-L11: each real transition logs its classification and outcome."""
    controller = _wire(_make_controller(), hooks)
    ctx = Context(user_id="abc")

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_vent_change(_event("off", "on", ctx))
        controller._on_vent_change(_event("on", "off"))

    messages = [r.message for r in caplog.records if r.name == ctrl_mod.__name__]
    assert (
        f"[room=office] Vent fan {VENT} off→on: user "
        f"(context id={ctx.id} user_id=abc parent_id=None) — no override"
    ) in messages
    assert any(
        m.startswith(f"[room=office] Vent fan {VENT} on→off: manual (context id=")
        and "— override off until " in m
        for m in messages
    )


def test_publish_sends_per_room_signal(monkeypatch):
    """CC-40: the status goes out on the per-entry, per-room signal."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        ctrl_mod, "async_dispatcher_send", lambda _hass, *args: sent.append(args)
    )
    controller = _make_controller()

    controller._publish_vent_override()
    controller._vent_override = True
    controller._publish_vent_override()

    signal = f"{SIGNAL_VENT_OVERRIDE}_entry1_office"
    assert sent == [(signal, "none", None), (signal, "on", None)]


# -- own command tagging (CC-39) ---------------------------------------------
def _vent_inputs(*, override):
    return EngineInputs(
        combined=False,
        room_temp=70.0,
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
        command_delay_ms=0,
        power_on_delay_ms=0,
        vent_fan=VentFanControl(
            entity_id=VENT,
            domain="switch",
            is_on=False,
            use=False,
            target=80.0,
            override=override,
        ),
    )


def test_run_tags_vent_command_with_own_context(hooks):
    """CC-39: a vent command carries a fresh context recorded in ``_vent_cmd``."""
    controller = _make_controller()
    controller._build_inputs = lambda: _vent_inputs(override=True)

    asyncio.run(controller._run("test"))

    calls = controller.hass.services.calls
    assert len(calls) == 1
    domain, service, data, kwargs = calls[0]
    assert (domain, service, data) == ("switch", "turn_on", {"entity_id": VENT})
    assert kwargs["blocking"] is True
    ctx = kwargs["context"]
    assert isinstance(ctx, Context)
    assert controller._vent_cmd is not None
    assert controller._vent_cmd[:2] == (ctx.id, True)

    # Its echo is the controller's own change, never a manual press.
    _wire(controller, hooks)
    controller._on_vent_change(_event("off", "on", ctx))
    assert controller._vent_override is None
    assert hooks["runs"] == []


def test_run_forgets_vent_command_on_service_failure():
    """CC-39: a failed vent call leaves no own-command record behind."""
    controller = _make_controller(fail=True)
    controller._build_inputs = lambda: _vent_inputs(override=True)

    asyncio.run(controller._run("test"))

    assert len(controller.hass.services.calls) == 1
    assert controller._vent_cmd is None


# -- echo allowance (CC-39) --------------------------------------------------
@pytest.fixture
def clock(monkeypatch):
    """Drive ``time.monotonic`` as seen by ``controller.py`` only."""
    now = [100.0]
    monkeypatch.setattr(ctrl_mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_fresh_context_echo_within_window_is_own(timers, hooks, clock):
    """CC-39: a fresh-context echo of the command within 10 s starts nothing."""
    controller = _wire(_make_controller(), hooks)
    controller._vent_cmd = ("cmd-id", True, clock[0])
    clock[0] += 3

    controller._on_vent_change(_event("off", "on"))

    assert timers == []
    assert hooks["published"] == []
    assert hooks["runs"] == []
    assert controller._vent_override is None
    assert controller._vent_cmd is None


def test_echo_allowance_is_one_shot(timers, hooks, clock):
    """CC-39: after the echo and a manual OFF, a manual ON is manual, not own."""
    controller = _wire(_make_controller(), hooks)
    cmd_ctx = Context()
    t0 = clock[0]
    # The rules command ON at t0 and the device echoes it.
    controller._vent_cmd = (cmd_ctx.id, True, t0)
    controller._on_vent_change(_event("off", "on", cmd_ctx))
    assert controller._vent_override is None
    assert timers == []

    # User presses OFF at t0+6: manual.
    clock[0] = t0 + 6
    controller._on_vent_change(_event("on", "off"))
    assert controller._vent_override is False
    assert len(timers) == 1

    # User presses ON at t0+9 (still inside the command's 10 s): manual too.
    clock[0] = t0 + 9
    controller._on_vent_change(_event("off", "on"))

    assert controller._vent_override is True
    timers[0][2].assert_called_once()
    assert len(timers) == 2
    assert timers[1][0] == VENT_OVERRIDE_SECONDS
    assert hooks["published"][-1][0] == "on"


def _vent_on_cmd():
    return SwitchTurnOn(entity_id=VENT)


def test_call_service_does_not_rearm_consumed_echo(clock):
    """CC-39: an echo seen during the call is not re-armed when the call returns."""
    controller = _make_controller()

    async def call(_domain, _service, _data, **kwargs):
        clock[0] += 1
        controller._on_vent_change(_event("off", "on", kwargs["context"]))

    controller.hass.services.async_call = call

    asyncio.run(
        controller._call_service(
            _vent_on_cmd(), "switch", "turn_on", {"entity_id": VENT}
        )
    )

    assert controller._vent_cmd is None
    assert controller._vent_override is None


def test_call_service_refreshes_echo_time_on_completion(clock):
    """CC-39: the 10 s echo window runs from when the call completes."""
    controller = _make_controller()
    seen: list[tuple] = []

    async def call(_domain, _service, _data, **_kwargs):
        seen.append(controller._vent_cmd)
        clock[0] = 130.0

    controller.hass.services.async_call = call

    asyncio.run(
        controller._call_service(
            _vent_on_cmd(), "switch", "turn_on", {"entity_id": VENT}
        )
    )

    assert seen[0][2] == 100.0
    assert controller._vent_cmd == (seen[0][0], True, 130.0)


def test_call_service_keeps_record_when_cancelled(clock):
    """CC-39: a cancelled vent call keeps its record; the device may still act."""
    controller = _make_controller()

    async def call(_domain, _service, _data, **_kwargs):
        raise asyncio.CancelledError

    controller.hass.services.async_call = call

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            controller._call_service(
                _vent_on_cmd(), "switch", "turn_on", {"entity_id": VENT}
            )
        )

    assert controller._vent_cmd is not None
    assert controller._vent_cmd[1:] == (True, clock[0])
