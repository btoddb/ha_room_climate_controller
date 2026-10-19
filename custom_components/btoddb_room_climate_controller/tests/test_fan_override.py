"""
Tests for the standalone fan manual override (CC-46/CC-47/CC-48, CC-L14).

Same approach as ``test_vent_fan_override.py``: ``controller.py`` imports
directly (Home Assistant is installed in this devcontainer), ``_resolve`` is
monkeypatched to bypass the entity registry, and ``hass`` is a tiny stand-in.
No live event loop: ``async_call_later`` is replaced with a recorder, and
``_publish_fan_override`` / ``async_request_run`` / ``_notify`` are patched
per test.
"""

import asyncio
import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import Context

from custom_components.btoddb_room_climate_controller import controller as ctrl_mod
from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.const import (
    FAN_ECHO_WINDOW_SECONDS,
    FAN_OVERRIDE_SECONDS,
    KEY_MANUAL_MODE,
    SIGNAL_FAN_OVERRIDE,
)
from custom_components.btoddb_room_climate_controller.controller import (
    RoomController,
    _classify_fan_change,
)
from custom_components.btoddb_room_climate_controller.engine import (
    Delay,
    EngineInputs,
    FanControl,
    FanInfo,
    FanSetPercentage,
    FanTurnOn,
    compute_commands,
)
from custom_components.btoddb_room_climate_controller.sensor import (
    FanOverrideSensor,
)

FAN = "fan.office_tower"
OTHER = "fan.office_ceiling"
TEMP = "sensor.office_temp"
MANUAL_MODE = f"switch.{KEY_MANUAL_MODE}"


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": False,
        "has_heater": False,
        "has_fan": True,
        "combined": False,
        "ac_climate": None,
        "heater_climate": None,
        "fan_entities": (FAN, OTHER),
        "ac_fan_entity": None,
        "heater_fan_entity": None,
        "ac_power_switch": None,
        "heater_power_switch": None,
        "temperature_sensor": TEMP,
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
    }
    defaults.update(overrides)
    return models.Room(**defaults)


class _StubState:
    def __init__(self, value, **attributes):
        self.state = str(value)
        self.attributes = attributes


class _StubStates:
    """``entity_id -> value`` or ``entity_id -> (value, attributes)``."""

    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        if value is None:
            return None
        if isinstance(value, tuple):
            return _StubState(value[0], **value[1])
        return _StubState(value)


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


def _st(value, **attributes):
    return _StubState(value, **attributes)


def _event(old, new, context=None, entity_id=FAN):
    """``old``/``new`` are ``_StubState`` instances or None."""
    return _FakeEvent(
        {"entity_id": entity_id, "old_state": old, "new_state": new},
        context or Context(),
    )


def _make_controller(values=None, *, fail=False):
    values = (
        {MANUAL_MODE: "off", TEMP: "70", FAN: "off", OTHER: "off"}
        if values is None
        else values
    )
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
    """Record ``_publish_fan_override``, ``async_request_run`` and ``_notify``."""
    return {"published": [], "runs": [], "notified": []}


def _wire(controller, hooks):
    def publish(entity_id):
        until = controller._fan_overrides.get(entity_id)
        hooks["published"].append((entity_id, "active" if until else "none", until))

    controller._publish_fan_override = publish
    controller.async_request_run = lambda trigger="evaluation": hooks["runs"].append(
        trigger
    )
    controller._notify = lambda device, message: hooks["notified"].append(
        (device, message)
    )
    return controller


@pytest.fixture
def clock(monkeypatch):
    """Drive ``time.monotonic`` as seen by ``controller.py`` only."""
    now = [100.0]
    monkeypatch.setattr(ctrl_mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


# -- classification (CC-47) --------------------------------------------------
def test_classify_on_off_transitions_are_manual():
    """CC-47: a context-less on<->off change is manual."""
    assert _classify_fan_change(_st("on"), _st("off"), Context(), None, 0.0) == (
        "manual"
    )
    assert _classify_fan_change(_st("off"), _st("on"), Context(), None, 0.0) == (
        "manual"
    )


def test_classify_speed_change_while_on_is_manual():
    """CC-47: with both sides on, a percentage or preset change is manual."""
    old = _st("on", percentage=33, preset_mode="normal")
    assert (
        _classify_fan_change(
            old, _st("on", percentage=66, preset_mode="normal"), Context(), None, 0.0
        )
        == "manual"
    )
    assert (
        _classify_fan_change(
            old, _st("on", percentage=33, preset_mode="sleep"), Context(), None, 0.0
        )
        == "manual"
    )


def test_classify_non_changes_are_ignored():
    """CC-47: attribute-only updates while off, other attributes, unavailable."""
    ctx = Context()
    # Attribute-only update while off.
    assert (
        _classify_fan_change(
            _st("off", percentage=0), _st("off", percentage=50), ctx, None, 0.0
        )
        == "ignored"
    )
    # On, but only an unrelated attribute (direction/oscillation) changed.
    assert (
        _classify_fan_change(
            _st("on", percentage=50, direction="forward"),
            _st("on", percentage=50, direction="reverse"),
            ctx,
            None,
            0.0,
        )
        == "ignored"
    )
    assert _classify_fan_change(_st("unavailable"), _st("on"), ctx, None, 0) == (
        "ignored"
    )
    assert _classify_fan_change(_st("on"), _st("unknown"), ctx, None, 0) == ("ignored")
    assert _classify_fan_change(None, _st("on"), ctx, None, 0) == "ignored"
    assert _classify_fan_change(_st("on"), None, ctx, None, 0) == "ignored"


def test_classify_user_and_automation_are_exempt():
    """CC-47: a user (dashboard) or context-carrying automation change is exempt."""
    assert (
        _classify_fan_change(_st("off"), _st("on"), Context(user_id="u"), None, 0.0)
        == "user"
    )
    assert (
        _classify_fan_change(
            _st("off"), _st("on"), Context(parent_id="parent"), None, 0.0
        )
        == "automation"
    )


def test_classify_own_context_id_is_own():
    """CC-47: the controller's own command context is its echo, before user/parent."""
    ctx = Context(user_id="u", parent_id="parent")
    own = (ctx.id, 0.0)
    assert _classify_fan_change(_st("off"), _st("on"), ctx, own, 999.0) == "own"
    assert _classify_fan_change(_st("on"), _st("off"), ctx, own, 999.0) == "own"


def test_classify_any_change_within_echo_window_is_own():
    """CC-47: any context-less change within 10 s of a command is own, any state."""
    own = ("other-id", 100.0)
    now = 100.0 + FAN_ECHO_WINDOW_SECONDS
    assert _classify_fan_change(_st("off"), _st("on"), Context(), own, now) == "own"
    assert _classify_fan_change(_st("on"), _st("off"), Context(), own, now) == "own"
    assert (
        _classify_fan_change(
            _st("on", percentage=10), _st("on", percentage=100), Context(), own, now
        )
        == "own"
    )


def test_classify_user_within_echo_window_is_user():
    """CC-47: the user/parent checks run before the echo window."""
    own = ("other-id", 100.0)
    assert (
        _classify_fan_change(_st("off"), _st("on"), Context(user_id="u"), own, 101.0)
        == "user"
    )


def test_classify_outside_echo_window_is_manual():
    """CC-47: the same change 11 s after the command is manual."""
    own = ("other-id", 100.0)
    assert (
        _classify_fan_change(_st("off"), _st("on"), Context(), own, 111.0) == "manual"
    )


# -- override start / restart / expiry (CC-46) -------------------------------
def test_manual_change_starts_override(timers, hooks, caplog):
    """CC-46: a manual change arms the 60 min timer, publishes, and re-evaluates."""
    controller = _wire(_make_controller(), hooks)

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_fan_change(_event(_st("off"), _st("on")))

    assert FAN_OVERRIDE_SECONDS == 60 * 60
    assert len(timers) == 1
    assert timers[0][0] == FAN_OVERRIDE_SECONDS
    assert FAN in controller._fan_overrides
    assert OTHER not in controller._fan_overrides
    assert hooks["published"] == [(FAN, "active", controller._fan_overrides[FAN])]
    assert hooks["runs"] == ["fan override started"]
    assert any(
        r.message.startswith(f"[room=office] Fan {FAN} off→on: manual (context id=")
        and "— override until " in r.message
        and r.levelno == logging.INFO
        for r in caplog.records
    )


def test_speed_change_while_on_starts_override(timers, hooks):
    """CC-46: pressing the speed button on a running fan starts an override."""
    controller = _wire(_make_controller(), hooks)

    controller._on_fan_change(
        _event(_st("on", percentage=33), _st("on", percentage=100))
    )

    assert FAN in controller._fan_overrides
    assert len(timers) == 1


def test_second_manual_change_restarts_override(timers, hooks, caplog):
    """CC-46: a further manual change restarts the full 60 minutes."""
    controller = _wire(_make_controller(), hooks)
    controller._on_fan_change(_event(_st("off"), _st("on")))
    first_cancel = timers[0][2]

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_fan_change(_event(_st("on"), _st("off")))

    first_cancel.assert_called_once()
    assert len(timers) == 2
    assert timers[1][0] == FAN_OVERRIDE_SECONDS
    assert hooks["runs"] == ["fan override started", "fan override started"]
    assert any("— override restarted until " in r.message for r in caplog.records)


def test_override_expiry_resumes_and_notifies(timers, hooks, caplog):
    """CC-46/CC-44: on expiry the override clears, notifies and re-evaluates."""
    values = {
        MANUAL_MODE: "off",
        TEMP: "70",
        FAN: ("on", {"friendly_name": "Basement Bathroom Tower Fan"}),
    }
    controller = _wire(_make_controller(values), hooks)
    controller._on_fan_change(_event(_st("off"), _st("on")))
    _delay, callback, _cancel = timers[0]

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        callback(None)

    assert controller._fan_overrides == {}
    assert controller._unsub_fan_timers == {}
    assert hooks["published"][-1] == (FAN, "none", None)
    assert hooks["runs"][-1] == "fan override expired"
    assert hooks["notified"] == [
        (
            "fan_fan_office_tower",
            "Basement Bathroom Tower Fan override ended — resuming climate control.",
        )
    ]
    assert f"[room=office] Fan override expired: {FAN}" in [
        r.message for r in caplog.records
    ]


def test_expiry_notification_falls_back_to_fan(timers, hooks):
    """CC-44: without a friendly name (or state) the message says "Fan"."""
    controller = _wire(_make_controller({MANUAL_MODE: "off"}), hooks)
    controller._on_fan_change(_event(_st("off"), _st("on")))

    timers[0][1](None)

    assert hooks["notified"] == [
        ("fan_fan_office_tower", "Fan override ended — resuming climate control.")
    ]


def test_notification_tag_uses_fan_slug():
    """CC-44: a fan override notification is tagged ``rcc-hold-<room>-fan_<slug>``."""
    controller = _make_controller()
    controller.hass.services.has_service = lambda _domain, _service: True
    controller.hass.async_create_task = asyncio.run

    controller._notify("fan_fan_office_tower", "Fan override ended")

    _domain, _service, data, _kwargs = controller.hass.services.calls[-1]
    assert data["tag"] == "rcc-hold-office-fan_fan_office_tower"
    assert data["message"] == "Fan override ended"
    assert data["title"] == "Room Climate · Office"


@pytest.mark.parametrize(
    "context", [Context(user_id="abc"), Context(parent_id="parent")]
)
def test_user_or_automation_change_starts_nothing(timers, hooks, caplog, context):
    """CC-47: user/automation changes start no override; logged at DEBUG."""
    controller = _wire(_make_controller(), hooks)

    with caplog.at_level(logging.DEBUG, logger=ctrl_mod.__name__):
        controller._on_fan_change(_event(_st("off"), _st("on"), context))

    assert timers == []
    assert hooks["published"] == []
    assert hooks["runs"] == []
    assert controller._fan_overrides == {}
    records = [r for r in caplog.records if f"Fan {FAN} off→on" in r.message]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].message.endswith("— no override")


def test_ignored_change_is_not_logged(timers, hooks, caplog):
    """CC-L14: ignored updates (attribute-only while off) are not logged."""
    controller = _wire(_make_controller(), hooks)

    with caplog.at_level(logging.DEBUG, logger=ctrl_mod.__name__):
        controller._on_fan_change(
            _event(_st("off", percentage=0), _st("off", percentage=50))
        )

    assert timers == []
    assert not [r for r in caplog.records if "Fan " in r.message]


# -- Manual Mode / lifecycle (CC-48) -----------------------------------------
def test_no_override_starts_in_manual_mode(timers, hooks, caplog):
    """CC-48: a manual change while Manual Mode is on starts nothing."""
    controller = _wire(_make_controller({MANUAL_MODE: "on", FAN: "on"}), hooks)

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_fan_change(_event(_st("off"), _st("on")))

    assert timers == []
    assert hooks["published"] == []
    assert hooks["runs"] == []
    assert controller._fan_overrides == {}
    assert any("no override (manual mode)" in r.message for r in caplog.records)


def test_manual_mode_clears_active_overrides(timers, hooks, caplog):
    """CC-48: turning Manual Mode on clears every fan override, silently."""
    controller = _wire(_make_controller(), hooks)
    controller._on_fan_change(_event(_st("off"), _st("on")))
    controller._on_fan_change(_event(_st("off"), _st("on"), entity_id=OTHER))
    cancels = [t[2] for t in timers]
    controller.hass.states._values[MANUAL_MODE] = "on"

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        assert controller._build_inputs() is None

    for cancel in cancels:
        cancel.assert_called_once()
    assert controller._fan_overrides == {}
    assert controller._unsub_fan_timers == {}
    assert hooks["published"][-2:] == [(FAN, "none", None), (OTHER, "none", None)]
    messages = [r.message for r in caplog.records]
    assert f"[room=office] Fan override cleared: {FAN}: manual mode" in messages
    assert f"[room=office] Fan override cleared: {OTHER}: manual mode" in messages
    assert hooks["notified"] == []


def test_resume_control_clears_fan_override(timers, hooks):
    """CC-48: Resume control ends an active fan override without notifying."""
    controller = _wire(_make_controller(), hooks)
    controller._on_fan_change(_event(_st("off"), _st("on")))

    assert controller.async_resume_control("button") is True

    timers[0][2].assert_called_once()
    assert controller._fan_overrides == {}
    assert hooks["published"][-1] == (FAN, "none", None)
    assert hooks["notified"] == []


def test_async_stop_cancels_timers_and_fan_listener(timers, hooks):
    """CC-48: stopping (unload/reload) drops the listener and pending timers."""
    controller = _wire(_make_controller(), hooks)
    controller._on_fan_change(_event(_st("off"), _st("on")))
    cancel = timers[0][2]
    unsub_fan = MagicMock()
    controller._unsub_fan = unsub_fan

    controller.async_stop()

    cancel.assert_called_once()
    unsub_fan.assert_called_once()
    assert controller._unsub_fan is None
    assert controller._unsub_fan_timers == {}
    assert controller._fan_overrides == {}


@pytest.mark.usefixtures("timers")
def test_async_start_registers_separate_fan_listener(monkeypatch, hooks):
    """CC-46: the physical fans get their own listener, outside the tracked set."""
    tracked: list[tuple[list[str], object]] = []

    def fake_track(_hass, entity_ids, cb):
        tracked.append((list(entity_ids), cb))
        return MagicMock()

    monkeypatch.setattr(ctrl_mod, "async_track_state_change_event", fake_track)
    controller = _wire(_make_controller(), hooks)

    controller.async_start()

    assert ([FAN, OTHER], controller._on_fan_change) in tracked
    assert FAN not in controller._tracked
    assert OTHER not in controller._tracked
    assert controller._unsub_fan is not None


# -- engine wiring (CC-46) ---------------------------------------------------
@pytest.mark.usefixtures("timers")
def test_build_inputs_marks_held_fan_and_engine_skips_it(hooks):
    """CC-46: only the overridden fan is held; the engine sends it nothing."""
    controller = _wire(
        _make_controller({MANUAL_MODE: "off", TEMP: "80", FAN: "off", OTHER: "off"}),
        hooks,
    )
    controller._start_fan_override(FAN)

    inputs = controller._build_inputs()

    assert inputs is not None
    held = {fan.info.entity_id: fan.held for fan in inputs.fans}
    assert held == {FAN: True, OTHER: False}
    # Make both fans "Use on" so the rules would turn both on at 80 °F.
    inputs = replace(inputs, fans=tuple(replace(fan, use=True) for fan in inputs.fans))
    commanded = {
        cmd.entity_id for cmd in compute_commands(inputs) if not isinstance(cmd, Delay)
    }
    assert commanded == {OTHER}


def test_publish_sends_per_fan_signal(monkeypatch):
    """CC-48: the status goes out on the per-entry, per-room, per-fan signal."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        ctrl_mod, "async_dispatcher_send", lambda _hass, *args: sent.append(args)
    )
    monkeypatch.setattr(ctrl_mod, "async_call_later", lambda *_a: MagicMock())
    controller = _make_controller()
    controller.async_request_run = MagicMock()

    controller._publish_fan_override(FAN)
    controller._start_fan_override(FAN)

    signal = f"{SIGNAL_FAN_OVERRIDE}_entry1_office_fan_office_tower"
    assert sent[0] == (signal, "none", None)
    assert sent[1] == (signal, "active", controller._fan_overrides[FAN])


# -- own command tagging (CC-47) ---------------------------------------------
def _fan_inputs():
    fan = FanControl(
        info=FanInfo(FAN, is_on=False, preset_mode=None, percentage=0, preset_modes=()),
        use=True,
        target=72.0,
        medium=75.0,
        high=78.0,
        reverse=False,
    )
    return EngineInputs(
        combined=False,
        room_temp=76.0,
        ac=None,
        heater=None,
        ac_fan=None,
        heater_fan=None,
        fans=(fan,),
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
    )


def test_run_tags_fan_commands_with_own_context(hooks, clock):
    """CC-47: each fan command carries a fresh context recorded in ``_fan_cmd``."""
    controller = _make_controller()
    controller._build_inputs = _fan_inputs

    asyncio.run(controller._run("test"))

    calls = controller.hass.services.calls
    assert [(d, s) for d, s, _data, _kw in calls] == [
        ("fan", "turn_on"),
        ("fan", "set_percentage"),
    ]
    last_ctx = calls[-1][3]["context"]
    assert isinstance(last_ctx, Context)
    assert controller._fan_cmd[FAN] == (last_ctx.id, clock[0])

    # Echoes of the sequence never start an override: the first (turn_on)
    # carries an older own context, the percentage poll arrives context-less.
    _wire(controller, hooks)
    controller._on_fan_change(_event(_st("off"), _st("on"), calls[0][3]["context"]))
    clock[0] += 5
    controller._on_fan_change(_event(_st("on", percentage=0), _st("on", percentage=50)))
    assert controller._fan_overrides == {}
    assert hooks["runs"] == []
    # The record survives "own" results (whole-window echo allowance).
    assert FAN in controller._fan_cmd


def test_run_forgets_fan_command_on_service_failure():
    """CC-47: a failed fan call leaves no own-command record behind."""
    controller = _make_controller(fail=True)
    controller._build_inputs = _fan_inputs

    asyncio.run(controller._run("test"))

    assert controller.hass.services.calls
    assert FAN not in controller._fan_cmd


def test_call_service_refreshes_echo_time_on_completion(clock):
    """CC-47: the 10 s echo window runs from when the call completes."""
    controller = _make_controller()
    seen: list[tuple] = []

    async def call(_domain, _service, _data, **_kwargs):
        seen.append(controller._fan_cmd[FAN])
        clock[0] = 130.0

    controller.hass.services.async_call = call

    asyncio.run(
        controller._call_service(FanTurnOn(FAN), "fan", "turn_on", {"entity_id": FAN})
    )

    assert seen[0][1] == 100.0
    assert controller._fan_cmd[FAN] == (seen[0][0], 130.0)


def test_call_service_keeps_record_when_cancelled(clock):
    """CC-47: a cancelled fan call keeps its record; the device may still act."""
    controller = _make_controller()

    async def call(_domain, _service, _data, **_kwargs):
        raise asyncio.CancelledError

    controller.hass.services.async_call = call

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            controller._call_service(
                FanSetPercentage(FAN, 50),
                "fan",
                "set_percentage",
                {"entity_id": FAN, "percentage": 50},
            )
        )

    assert controller._fan_cmd[FAN][1] == clock[0]


def _vent_and_fan_controller(*, fail=False):
    """Build a controller whose FAN is both the vent fan and a standalone fan."""
    hass = _StubHass({MANUAL_MODE: "off", TEMP: "70", FAN: "off"}, fail=fail)
    entry = MagicMock()
    entry.entry_id = "entry1"
    controller = RoomController(
        hass, entry, _room(has_vent_fan=True, vent_fan_entity=FAN)
    )
    controller._resolve = lambda key, domain: f"{domain}.{key}"
    return controller


def test_call_service_records_fan_command_for_vent_fan_too(timers, hooks, clock):
    """CC-47: a fan that is also the vent fan gets both records; echoes are own."""
    controller = _vent_and_fan_controller()

    asyncio.run(
        controller._call_service(FanTurnOn(FAN), "fan", "turn_on", {"entity_id": FAN})
    )

    ctx = controller.hass.services.calls[-1][3]["context"]
    assert controller._vent_cmd == (ctx.id, True, clock[0])
    assert controller._fan_cmd[FAN] == (ctx.id, clock[0])

    # A context-less report within the echo window is the controller's own.
    _wire(controller, hooks)
    clock[0] += 3
    controller._on_fan_change(_event(_st("off"), _st("on")))
    assert controller._fan_overrides == {}
    assert timers == []


def test_call_service_failure_forgets_both_records():
    """CC-47/CC-39: a failed call to a vent-and-standalone fan leaves no record."""
    controller = _vent_and_fan_controller(fail=True)

    with pytest.raises(RuntimeError):
        asyncio.run(
            controller._call_service(
                FanTurnOn(FAN), "fan", "turn_on", {"entity_id": FAN}
            )
        )

    assert controller._vent_cmd is None
    assert FAN not in controller._fan_cmd


def test_manual_change_after_echo_window_starts_override(timers, hooks, clock):
    """CC-47: a context-less change 11 s after the last command is manual."""
    controller = _wire(_make_controller(), hooks)
    controller._fan_cmd[FAN] = ("cmd-id", clock[0])
    clock[0] += FAN_ECHO_WINDOW_SECONDS + 1

    controller._on_fan_change(_event(_st("on"), _st("off")))

    assert FAN in controller._fan_overrides
    assert FAN not in controller._fan_cmd
    assert len(timers) == 1


# -- status sensor (CC-48) ---------------------------------------------------
def test_fan_override_sensor_identity_and_updates():
    """CC-48: per-fan sensor, none/active with an ISO ``until`` attribute."""
    entry = SimpleNamespace(entry_id="entry1")
    sensor = FanOverrideSensor(entry, _room(), FAN)

    assert sensor.unique_id == models.room_uid(
        "entry1", "office", "fan_override__fan_office_tower"
    )
    assert sensor.name == "Office Tower override"
    assert sensor.options == ["none", "active"]
    assert sensor.native_value == "none"
    assert sensor.extra_state_attributes == {"until": None}
    assert sensor._signal == f"{SIGNAL_FAN_OVERRIDE}_entry1_office_fan_office_tower"

    sensor.async_write_ha_state = MagicMock()
    until = ctrl_mod.dt_util.utcnow()
    sensor._handle_override("active", until)
    assert sensor.native_value == "active"
    assert sensor.extra_state_attributes == {"until": until.isoformat()}
    sensor._handle_override("none", None)
    assert sensor.native_value == "none"
    assert sensor.extra_state_attributes == {"until": None}
    assert sensor.async_write_ha_state.call_count == 2
