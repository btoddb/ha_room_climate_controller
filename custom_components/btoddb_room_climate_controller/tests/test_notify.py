"""
Tests for hold-ended notifications (CC-44).

``notify.py`` imports directly (Home Assistant is installed in this
devcontainer); ``hass`` is a tiny stand-in exposing only
``services.has_service`` / ``services.async_call``.
"""

import asyncio
import logging

import pytest

from custom_components.btoddb_room_climate_controller import notify
from custom_components.btoddb_room_climate_controller.const import (
    NOTIFY_DOMAIN,
    NOTIFY_SERVICE,
)


class _StubServices:
    def __init__(self, *, registered=True, fail=False):
        self.calls: list[tuple[str, str, dict, dict]] = []
        self._registered = registered
        self._fail = fail

    def has_service(self, domain, service):
        return self._registered and (domain, service) == (NOTIFY_DOMAIN, NOTIFY_SERVICE)

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, data, kwargs))
        if self._fail:
            msg = "simulated failure"
            raise RuntimeError(msg)


class _StubHass:
    def __init__(self, **kwargs):
        self.services = _StubServices(**kwargs)


class _Room:
    key = "office"
    label = "Office"


@pytest.fixture(autouse=True)
def _reset_warned():
    notify._WARNED.clear()
    yield
    notify._WARNED.clear()


def _send(hass, device="ac", message="A/C hold ended — resuming climate control."):
    asyncio.run(notify.async_send_hold_ended(hass, _Room(), device, message))


def test_sends_via_btoddb_notifications():
    """CC-44: the message goes to btoddb_notifications.send with title and tag."""
    hass = _StubHass()

    _send(hass, "heater", "Heater hold ended — resuming climate control.")

    assert hass.services.calls == [
        (
            "btoddb_notifications",
            "send",
            {
                "message": "Heater hold ended — resuming climate control.",
                "title": "Room Climate · Office",
                "tag": "rcc-hold-office-heater",
            },
            {"blocking": True},
        )
    ]


def test_missing_service_warns_once_and_skips(caplog):
    """CC-44: without the service nothing is called; one WARNING per process."""
    hass = _StubHass(registered=False)

    with caplog.at_level(logging.WARNING, logger=notify.__name__):
        _send(hass)
        _send(hass, "vent_fan", "Vent fan override ended — resuming climate control.")

    assert hass.services.calls == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        "[room=office] btoddb_notifications.send is not registered; "
        "hold-ended notifications are skipped"
    )


def test_failing_call_is_swallowed_and_logged(caplog):
    """CC-44: a failing notification never raises into the controller."""
    hass = _StubHass(fail=True)

    with caplog.at_level(logging.ERROR, logger=notify.__name__):
        _send(hass)

    assert len(hass.services.calls) == 1
    assert any(
        r.getMessage() == "[room=office] hold-ended notification failed"
        and r.exc_info is not None
        for r in caplog.records
    )
