"""
Tests for the thermostat setpoint hold (CC-41..CC-44, CC-L12).

Same approach as ``test_vent_fan_override.py``: ``controller.py`` imports
directly (Home Assistant is installed in this devcontainer), ``_resolve`` is
monkeypatched to bypass the entity registry, and ``hass`` is a tiny stand-in.
No live event loop: ``async_call_later`` is replaced with a recorder, and
``_publish_setpoint_hold`` / ``async_request_run`` / ``_notify`` are patched
per test.
"""

import asyncio
import dataclasses
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util

from custom_components.btoddb_room_climate_controller import controller as ctrl_mod
from custom_components.btoddb_room_climate_controller import models
from custom_components.btoddb_room_climate_controller.const import (
    KEY_MANUAL_MODE,
    SETPOINT_ECHO_WINDOW_SECONDS,
    SIGNAL_SETPOINT_HOLD,
)
from custom_components.btoddb_room_climate_controller.controller import (
    RoomController,
    _classify_climate_change,
)
from custom_components.btoddb_room_climate_controller.engine import (
    ClimateInfo,
    EngineInputs,
    FanTurnOn,
    SetHvacMode,
    SetTemperature,
)

AC = "climate.office_ac"
HEATER = "climate.office_heater"
AC_FAN = "fan.office_ac_fan"
MANUAL_MODE = f"switch.{KEY_MANUAL_MODE}"


def _room(**overrides):
    defaults = {
        "room_id": "sub1",
        "key": "office",
        "label": "Office",
        "area_id": None,
        "has_ac": True,
        "has_heater": True,
        "has_fan": False,
        "combined": False,
        "ac_climate": AC,
        "heater_climate": HEATER,
        "fan_entities": (),
        "ac_fan_entity": AC_FAN,
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
        "setpoint_hold_minutes": 60,
        "notify_on_hold_end": True,
    }
    defaults.update(overrides)
    return models.Room(**defaults)


class _StubState:
    def __init__(self, value, attributes=None):
        self.state = str(value)
        self.attributes = attributes or {}


def _climate_state(setpoint, hvac="cool"):
    attrs = {"hvac_mode": hvac, "hvac_modes": ["off", "cool", "heat"]}
    if setpoint is not None:
        attrs["temperature"] = setpoint
    return _StubState(hvac, attrs)


class _StubStates:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        if value is None or isinstance(value, _StubState):
            return value
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
        self.tasks: list[str] = []

    def async_create_task(self, coro, _name=None):
        """Record the scheduled coroutine's name without running it."""
        self.tasks.append(coro.__name__)
        coro.close()


class _FakeEvent:
    """Minimal ``Event`` stand-in: ``data`` plus a real ``Context``."""

    def __init__(self, data, context):
        self.data = data
        self.context = context


def _event(old, new, context=None, entity_id=AC, *, hvac="cool"):
    return _FakeEvent(
        {
            "entity_id": entity_id,
            "old_state": _climate_state(old, hvac),
            "new_state": _climate_state(new, hvac),
        },
        context or Context(),
    )


def _make_controller(values=None, *, fail=False, **room_overrides):
    values = {MANUAL_MODE: "off"} if values is None else values
    hass = _StubHass(values, fail=fail)
    entry = MagicMock()
    entry.entry_id = "entry1"
    controller = RoomController(hass, entry, _room(**room_overrides))
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
    """Record ``_publish_setpoint_hold``, ``async_request_run`` and ``_notify``."""
    return {"published": [], "runs": [], "notified": []}


@pytest.fixture
def clock(monkeypatch):
    """Drive ``time.monotonic`` as seen by ``controller.py`` only."""
    now = [100.0]
    monkeypatch.setattr(ctrl_mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def _wire(controller, hooks):
    def publish(entity_id):
        hold = controller._setpoint_holds.get(entity_id)
        if hold is None:
            hooks["published"].append((entity_id, "none", None, None))
        else:
            hooks["published"].append((entity_id, "active", hold[0], hold[1]))

    controller._publish_setpoint_hold = publish
    controller.async_request_run = lambda trigger="evaluation": hooks["runs"].append(
        trigger
    )
    controller._notify = lambda device, message: hooks["notified"].append(
        (device, message)
    )
    return controller


# -- classification (CC-42) --------------------------------------------------
def _cs(setpoint, hvac="cool"):
    return _climate_state(setpoint, hvac)


def test_classify_non_setpoint_changes_are_ignored():
    """CC-42: mode-only, unavailable, missing and unchanged setpoints are ignored."""
    ctx = Context()
    # HVAC-mode-only update: same setpoint.
    assert _classify_climate_change(_cs(70, "off"), _cs(70, "cool"), ctx, None, 0) == (
        "ignored"
    )
    assert _classify_climate_change(_cs(70), _cs(70), ctx, None, 0) == "ignored"
    unavailable = _StubState("unavailable", {"temperature": 68})
    assert _classify_climate_change(_cs(70), unavailable, ctx, None, 0) == "ignored"
    assert _classify_climate_change(unavailable, _cs(70), ctx, None, 0) == "ignored"
    assert _classify_climate_change(None, _cs(70), ctx, None, 0) == "ignored"
    assert _classify_climate_change(_cs(70), _cs(None), ctx, None, 0) == "ignored"
    garbled = _StubState("cool", {"temperature": "n/a"})
    assert _classify_climate_change(_cs(70), garbled, ctx, None, 0) == "ignored"


def test_classify_own_context_id_beats_parent():
    """CC-42: the controller's own command context wins over a parent id."""
    ctx = Context(parent_id="parent")
    own = (ctx.id, 70, 0.0)
    assert _classify_climate_change(_cs(72), _cs(65), ctx, own, 999.0) == "own"


def test_classify_parent_context_is_automation():
    """CC-42: an automation never starts a hold, even inside the echo window."""
    ctx = Context(parent_id="parent")
    assert _classify_climate_change(_cs(72), _cs(65), ctx, None, 0.0) == "automation"
    own = ("other-id", 70, 100.0)
    assert _classify_climate_change(_cs(72), _cs(70), ctx, own, 101.0) == "automation"


def test_classify_user_context_is_manual():
    """CC-42: a change made in the HA app (user context only) is manual."""
    ctx = Context(user_id="abc")
    assert _classify_climate_change(_cs(72), _cs(65), ctx, None, 0.0) == "manual"


def test_classify_echo_within_window_and_tolerance_is_own():
    """CC-42: a fresh-context echo near the commanded value within 10 s is own."""
    own = ("other-id", 70, 100.0)
    now = 100.0 + SETPOINT_ECHO_WINDOW_SECONDS
    assert _classify_climate_change(_cs(72), _cs(70.5), Context(), own, now) == "own"
    assert _classify_climate_change(_cs(72), _cs(69.1), Context(), own, now) == "own"


def test_classify_echo_exactly_one_degree_off_is_manual():
    """CC-42: the tolerance is strict — exactly 1.0 °F off is manual."""
    own = ("other-id", 70, 100.0)
    assert (
        _classify_climate_change(_cs(72), _cs(71.0), Context(), own, 101.0) == "manual"
    )


def test_classify_echo_after_window_is_manual():
    """CC-42: the commanded value arriving after 10 s is a manual change."""
    own = ("other-id", 70, 100.0)
    now = 100.0 + SETPOINT_ECHO_WINDOW_SECONDS + 0.01
    assert _classify_climate_change(_cs(72), _cs(70), Context(), own, now) == "manual"


def test_classify_non_setpoint_command_window_is_own():
    """CC-42: any change within 10 s of a mode/off command is its late echo."""
    own = ("other-id", None, 100.0)
    now = 100.0 + SETPOINT_ECHO_WINDOW_SECONDS
    assert _classify_climate_change(_cs(68), _cs(74), Context(), own, now) == "own"


def test_classify_non_setpoint_command_after_window_is_manual():
    """CC-42: past the 10 s window a mode/off command grants no allowance."""
    own = ("other-id", None, 100.0)
    now = 100.0 + SETPOINT_ECHO_WINDOW_SECONDS + 0.01
    assert _classify_climate_change(_cs(68), _cs(74), Context(), own, now) == "manual"


# -- own command tagging (CC-42) ---------------------------------------------
def test_call_service_records_set_temperature(clock):
    """CC-42: a SetTemperature is recorded with its setpoint before the await."""
    controller = _make_controller()
    seen: list[tuple] = []

    async def call(_domain, _service, _data, **kwargs):
        seen.append((controller._climate_cmd[AC], kwargs["context"]))
        clock[0] = 103.0

    controller.hass.services.async_call = call

    asyncio.run(
        controller._call_service(
            SetTemperature(AC, 70, "cool"),
            "climate",
            "set_temperature",
            {"entity_id": AC, "temperature": 70, "hvac_mode": "cool"},
        )
    )

    record, ctx = seen[0]
    assert isinstance(ctx, Context)
    assert record == (ctx.id, 70, 100.0)
    # Refreshed on completion so the echo window runs from there.
    assert controller._climate_cmd[AC] == (ctx.id, 70, 103.0)


def test_call_service_records_non_setpoint_command_without_value(clock):
    """CC-42: SetHvacMode is recorded too, with no setpoint."""
    controller = _make_controller()

    asyncio.run(
        controller._call_service(
            SetHvacMode(HEATER, "heat"),
            "climate",
            "set_hvac_mode",
            {"entity_id": HEATER, "hvac_mode": "heat"},
        )
    )

    (_domain, _service, _data, kwargs) = controller.hass.services.calls[0]
    assert controller._climate_cmd[HEATER] == (kwargs["context"].id, None, clock[0])


def test_call_service_passes_context_on_fan_command():
    """Every command carries a fresh context; only climate commands are recorded."""
    controller = _make_controller()

    asyncio.run(
        controller._call_service(
            FanTurnOn(AC_FAN), "fan", "turn_on", {"entity_id": AC_FAN}
        )
    )

    (_domain, _service, _data, kwargs) = controller.hass.services.calls[0]
    assert kwargs["blocking"] is True
    assert isinstance(kwargs["context"], Context)
    assert controller._climate_cmd == {}


def test_call_service_drops_record_on_failure():
    """CC-42: a failed climate call leaves no own-command record behind."""
    controller = _make_controller(fail=True)

    with pytest.raises(RuntimeError):
        asyncio.run(
            controller._call_service(
                SetTemperature(AC, 70, "cool"),
                "climate",
                "set_temperature",
                {"entity_id": AC, "temperature": 70, "hvac_mode": "cool"},
            )
        )

    assert controller._climate_cmd == {}


def test_call_service_keeps_record_when_cancelled(clock):
    """CC-42: a cancelled climate call keeps its record; the device may still act."""
    controller = _make_controller()

    async def call(_domain, _service, _data, **_kwargs):
        raise asyncio.CancelledError

    controller.hass.services.async_call = call

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            controller._call_service(
                SetTemperature(AC, 70, "cool"),
                "climate",
                "set_temperature",
                {"entity_id": AC, "temperature": 70, "hvac_mode": "cool"},
            )
        )

    assert controller._climate_cmd[AC][1:] == (70, clock[0])


def _ac_inputs():
    return EngineInputs(
        combined=False,
        room_temp=80.0,
        ac=ClimateInfo(
            entity_id=AC,
            hvac_mode="cool",
            fan_mode=None,
            hvac_modes=("off", "cool"),
            fan_modes=(),
            min_temp=None,
            supports_set_temp=True,
            current_setpoint=74.0,
        ),
        heater=None,
        ac_fan=None,
        heater_fan=None,
        fans=(),
        ac_power=None,
        heater_power=None,
        use_ac=True,
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


@pytest.mark.usefixtures("timers")
def test_run_echo_of_own_setpoint_starts_no_hold(hooks, clock):
    """CC-42: RCC's own setpoint and its later context-less poll are both own."""
    controller = _make_controller()
    controller._build_inputs = _ac_inputs

    asyncio.run(controller._run("test"))

    (_domain, service, data, kwargs) = controller.hass.services.calls[0]
    assert (service, data["temperature"]) == ("set_temperature", 70)
    ctx = kwargs["context"]
    _wire(controller, hooks)

    controller._on_climate_change(_event(74, 70, ctx))
    # "own" keeps the record: a later context-less poll with a rounded value
    # inside the window is still the echo.
    assert controller._climate_cmd[AC][0] == ctx.id
    clock[0] += 3
    controller._on_climate_change(_event(70, 70.4))

    assert controller._setpoint_holds == {}
    assert hooks["runs"] == []


def test_mode_switch_surfacing_stored_setpoint_starts_no_hold(timers, hooks, clock):
    """
    CC-42: a heat pump's stored per-mode setpoint after SetHvacMode is own.

    Combined heat pump in heat@68 with a stored cool setpoint of 74: after RCC
    switches it to cool, a context-less poll reports 68→74. That must not start
    a hold (which would cancel the run before SetTemperature(70) is sent).
    """
    controller = _wire(
        _make_controller(combined=True, heater_climate=AC, ac_fan_entity=None), hooks
    )
    asyncio.run(
        controller._call_service(
            SetHvacMode(AC, "cool"),
            "climate",
            "set_hvac_mode",
            {"entity_id": AC, "hvac_mode": "cool"},
        )
    )
    clock[0] += 5

    controller._on_climate_change(_event(68, 74))

    assert controller._setpoint_holds == {}
    assert timers == []
    assert hooks["runs"] == []


def test_hold_expiry_reasserts_setpoint_and_echo_starts_no_hold(timers, hooks, clock):
    """CC-41/CC-19/CC-42: expiry → memory cleared → re-send → own echo, no hold."""
    controller = _wire(_make_controller(), hooks)
    controller._last_commanded_setpoints[AC] = ("cool", 70)
    controller._on_climate_change(_event(70, 68))
    assert AC in controller._setpoint_holds

    timers[0][1](None)

    assert controller._setpoint_holds == {}
    assert AC not in controller._last_commanded_setpoints
    inputs = _ac_inputs()
    controller._build_inputs = lambda: dataclasses.replace(
        inputs, ac=dataclasses.replace(inputs.ac, current_setpoint=68.0)
    )
    asyncio.run(controller._run("setpoint hold expired"))

    (_domain, service, data, kwargs) = controller.hass.services.calls[0]
    assert (service, data["temperature"]) == ("set_temperature", 70)
    runs_before = list(hooks["runs"])
    clock[0] += 1

    controller._on_climate_change(_event(68, 70, kwargs["context"]))

    assert controller._setpoint_holds == {}
    assert len(timers) == 1
    assert hooks["runs"] == runs_before


# -- hold lifecycle (CC-41) --------------------------------------------------
def test_manual_change_starts_hold(timers, hooks):
    """CC-41: a manual setpoint change holds the entity, publishes and re-runs."""
    controller = _wire(_make_controller(), hooks)

    controller._on_climate_change(_event(70, 68))

    until, setpoint = controller._setpoint_holds[AC]
    assert setpoint == 68.0
    assert len(timers) == 1
    assert timers[0][0] == 60 * 60
    assert hooks["published"] == [(AC, "active", until, 68.0)]
    assert hooks["runs"] == ["setpoint hold started"]
    assert HEATER not in controller._setpoint_holds


def test_manual_change_consumes_command_record(timers, hooks, clock):
    """CC-42: a manual change supersedes the controller's command record."""
    controller = _wire(_make_controller(), hooks)
    controller._climate_cmd[AC] = ("cmd-id", 70, clock[0] - 60)

    controller._on_climate_change(_event(70, 66))

    assert AC not in controller._climate_cmd
    assert len(timers) == 1


def test_second_manual_change_restarts_hold(timers, hooks, caplog):
    """CC-41: a further manual change restarts the hold with the new setpoint."""
    controller = _wire(_make_controller(), hooks)
    controller._on_climate_change(_event(70, 68))
    first_cancel = timers[0][2]

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_climate_change(_event(68, 66))

    first_cancel.assert_called_once()
    assert len(timers) == 2
    assert timers[1][0] == 60 * 60
    assert controller._setpoint_holds[AC][1] == 66.0
    assert hooks["published"][-1][1:] == (
        "active",
        controller._setpoint_holds[AC][0],
        66.0,
    )
    assert any("— hold restarted until " in r.message for r in caplog.records)


def test_hold_expiry_resumes_control(timers, hooks):
    """CC-41/CC-44: expiry clears memory, publishes, notifies and re-runs."""
    controller = _wire(_make_controller(), hooks)
    controller._last_commanded_setpoints[HEATER] = ("heat", 70)
    controller._last_commanded_setpoints[AC] = ("cool", 70)
    controller._on_climate_change(_event(70, 74, entity_id=HEATER, hvac="heat"))

    _delay, callback, _cancel = timers[0]
    callback(None)

    assert controller._setpoint_holds == {}
    assert controller._unsub_hold_timers == {}
    assert HEATER not in controller._last_commanded_setpoints
    assert controller._last_commanded_setpoints[AC] == ("cool", 70)
    assert hooks["published"][-1] == (HEATER, "none", None, None)
    assert hooks["runs"][-1] == "setpoint hold expired"
    assert hooks["notified"] == [
        ("heater", "Heater hold ended — resuming climate control.")
    ]


def test_ac_hold_expiry_notifies_ac(timers, hooks):
    """CC-44: an A/C hold's notification names the A/C."""
    controller = _wire(_make_controller(), hooks)
    controller._on_climate_change(_event(70, 68))

    timers[0][1](None)

    assert hooks["notified"] == [("ac", "A/C hold ended — resuming climate control.")]


def test_no_hold_starts_in_manual_mode(timers, hooks, caplog):
    """CC-43: a manual change while Manual Mode is on starts nothing."""
    controller = _wire(_make_controller({MANUAL_MODE: "on"}), hooks)

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        controller._on_climate_change(_event(70, 68))

    assert timers == []
    assert hooks["published"] == []
    assert hooks["runs"] == []
    assert controller._setpoint_holds == {}
    assert any("— no hold (manual mode)" in r.message for r in caplog.records)


def test_manual_mode_clears_active_holds_without_notifying(timers, hooks, caplog):
    """CC-43/CC-44: Manual Mode clears every hold and its timer, silently."""
    controller = _wire(_make_controller(), hooks)
    controller._on_climate_change(_event(70, 68))
    controller._on_climate_change(_event(70, 74, entity_id=HEATER, hvac="heat"))
    cancels = [t[2] for t in timers]
    controller.hass.states._values[MANUAL_MODE] = "on"

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        assert controller._build_inputs() is None

    for cancel in cancels:
        cancel.assert_called_once()
    assert controller._setpoint_holds == {}
    assert controller._unsub_hold_timers == {}
    assert (AC, "none", None, None) in hooks["published"]
    assert (HEATER, "none", None, None) in hooks["published"]
    assert hooks["notified"] == []
    messages = [r.message for r in caplog.records]
    assert f"[room=office] Setpoint hold cleared: {AC}: manual mode" in messages
    assert f"[room=office] Setpoint hold cleared: {HEATER}: manual mode" in messages


@pytest.mark.usefixtures("timers")
def test_async_start_subscribes_climate_entities(monkeypatch, hooks):
    """CC-41: one separate listener for the climate entities, outside _tracked."""
    tracked: list[tuple[list[str], object]] = []

    def fake_track(_hass, entity_ids, cb):
        tracked.append((list(entity_ids), cb))
        return MagicMock()

    monkeypatch.setattr(ctrl_mod, "async_track_state_change_event", fake_track)
    controller = _wire(_make_controller(), hooks)

    controller.async_start()

    assert ([AC, HEATER], controller._on_climate_change) in tracked
    assert AC not in controller._tracked
    assert HEATER not in controller._tracked
    assert controller._unsub_climate is not None


@pytest.mark.usefixtures("timers")
def test_async_start_without_hold_has_no_climate_listener(monkeypatch, hooks):
    """CC-41: with the hold disabled no climate listener is registered."""
    tracked: list[tuple[list[str], object]] = []

    def fake_track(_hass, entity_ids, cb):
        tracked.append((list(entity_ids), cb))
        return MagicMock()

    monkeypatch.setattr(ctrl_mod, "async_track_state_change_event", fake_track)
    controller = _wire(_make_controller(setpoint_hold_minutes=0), hooks)

    controller.async_start()

    assert all(cb != controller._on_climate_change for _ids, cb in tracked)
    assert controller._unsub_climate is None


@pytest.mark.usefixtures("timers")
def test_combined_room_subscribes_once_and_uses_ac_role(monkeypatch, hooks):
    """CC-41/CC-43: a combined heat pump is one entity, reported as "ac"."""
    tracked: list[tuple[list[str], object]] = []

    def fake_track(_hass, entity_ids, cb):
        tracked.append((list(entity_ids), cb))
        return MagicMock()

    monkeypatch.setattr(ctrl_mod, "async_track_state_change_event", fake_track)
    controller = _wire(
        _make_controller(combined=True, heater_climate=AC, ac_fan_entity=None), hooks
    )

    controller.async_start()

    assert controller._climate_entities == frozenset({AC})
    climate_subs = [ids for ids, cb in tracked if cb == controller._on_climate_change]
    assert climate_subs == [[AC]]
    assert controller._hold_role(AC) == "ac"


def test_async_stop_cancels_hold_timers_and_listener(timers, hooks):
    """CC-41: stopping (unload/reload) drops the listener, timers and records."""
    controller = _wire(_make_controller(), hooks)
    controller._on_climate_change(_event(70, 68))
    cancel = timers[0][2]
    unsub = MagicMock()
    controller._unsub_climate = unsub
    controller._climate_cmd[HEATER] = ("cmd-id", 70, 0.0)

    controller.async_stop()

    cancel.assert_called_once()
    unsub.assert_called_once()
    assert controller._unsub_climate is None
    assert controller._unsub_hold_timers == {}
    assert controller._setpoint_holds == {}
    assert controller._climate_cmd == {}


def test_climate_info_reports_held(timers, hooks):
    """CC-41: the engine sees ``held`` for exactly the held entity."""
    controller = _wire(
        _make_controller({MANUAL_MODE: "off", AC: _cs(70), HEATER: _cs(70, "heat")}),
        hooks,
    )
    assert controller._climate_info(AC).held is False

    controller._on_climate_change(_event(70, 68))

    assert controller._climate_info(AC).held is True
    assert controller._climate_info(HEATER).held is False
    assert len(timers) == 1


def test_publish_sends_per_room_per_role_signal(monkeypatch):
    """CC-43: the status goes out on the per-entry, per-room, per-role signal."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        ctrl_mod, "async_dispatcher_send", lambda _hass, *args: sent.append(args)
    )
    controller = _make_controller()
    until = dt_util.utcnow()

    controller._publish_setpoint_hold(AC)
    controller._setpoint_holds[AC] = (until, 68.0)
    controller._publish_setpoint_hold(AC)
    controller._publish_setpoint_hold(HEATER)

    ac_signal = f"{SIGNAL_SETPOINT_HOLD}_entry1_office_ac"
    heater_signal = f"{SIGNAL_SETPOINT_HOLD}_entry1_office_heater"
    assert sent == [
        (ac_signal, "none", None, None),
        (ac_signal, "active", until, 68.0),
        (heater_signal, "none", None, None),
    ]


# -- notification scheduling (CC-44) -----------------------------------------
def test_notify_schedules_send_when_enabled():
    """CC-44: _notify hands the send to a background task."""
    controller = _make_controller()

    controller._notify("ac", "A/C hold ended — resuming climate control.")

    assert controller.hass.tasks == ["async_send_hold_ended"]


def test_notify_disabled_schedules_nothing():
    """CC-44: notify_on_hold_end=False suppresses every hold-ended notification."""
    controller = _make_controller(notify_on_hold_end=False)

    controller._notify("ac", "A/C hold ended — resuming climate control.")

    assert controller.hass.tasks == []


# -- logging (CC-L12) --------------------------------------------------------
@pytest.mark.usefixtures("timers")
def test_classification_is_logged(hooks, caplog):
    """CC-L12: manual changes log at INFO, own/automation at DEBUG, ignored not."""
    controller = _wire(_make_controller(), hooks)
    auto_ctx = Context(parent_id="parent")
    manual_ctx = Context(user_id="abc")

    with caplog.at_level(logging.DEBUG, logger=ctrl_mod.__name__):
        controller._on_climate_change(_event(70, 72, auto_ctx))
        controller._on_climate_change(_event(72, 72, entity_id=HEATER))
        controller._on_climate_change(_event(72, 68, manual_ctx))

    records = [
        (r.levelno, r.message) for r in caplog.records if r.name == ctrl_mod.__name__
    ]
    assert (
        logging.DEBUG,
        (
            f"[room=office] Climate {AC} setpoint 70.0→72.0: automation "
            f"(context id={auto_ctx.id} parent_id=parent) — no hold"
        ),
    ) in records
    assert not any(HEATER in msg for _level, msg in records)
    until = dt_util.as_local(controller._setpoint_holds[AC][0])
    assert (
        logging.INFO,
        (
            f"[room=office] Climate {AC} setpoint 72.0→68.0: manual "
            f"(context id={manual_ctx.id} parent_id=None) — hold until {until:%H:%M}"
        ),
    ) in records


def test_expiry_is_logged(timers, hooks, caplog):
    """CC-L12: hold expiry logs at INFO."""
    controller = _wire(_make_controller(), hooks)
    controller._on_climate_change(_event(70, 68))

    with caplog.at_level(logging.INFO, logger=ctrl_mod.__name__):
        timers[0][1](None)

    assert f"[room=office] Setpoint hold expired: {AC}" in [
        r.message for r in caplog.records
    ]
