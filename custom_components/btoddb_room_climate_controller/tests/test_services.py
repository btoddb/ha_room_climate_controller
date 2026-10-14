"""
Tests for the ``resume_control`` service handler (CC-45).

The package ``__init__`` imports directly (Home Assistant is installed in this
devcontainer); the module-level ``_resume_control(hass, call)`` handler is
called with a stand-in ``hass`` whose ``config_entries`` lists fake entries.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError

from custom_components.btoddb_room_climate_controller import _resume_control
from custom_components.btoddb_room_climate_controller.const import DOMAIN


class _StubConfigEntries:
    def __init__(self, entries):
        self._entries = entries

    def async_entries(self, domain):
        assert domain == DOMAIN
        return list(self._entries)


def _hass(*entries):
    return SimpleNamespace(config_entries=_StubConfigEntries(entries))


def _entry(controllers, state=ConfigEntryState.LOADED):
    return SimpleNamespace(
        state=state, runtime_data=SimpleNamespace(controllers=controllers)
    )


def _call(room):
    return SimpleNamespace(data={"room": room})


def test_known_room_resumes_control():
    """CC-45: the service resumes the named room's controller."""
    controller = MagicMock()
    other = MagicMock()
    hass = _hass(_entry({"office": controller, "den": other}))

    _resume_control(hass, _call("office"))

    controller.async_resume_control.assert_called_once_with("service")
    other.async_resume_control.assert_not_called()


def test_unknown_room_raises():
    """CC-45: an unknown room key is a validation error, not a silent no-op."""
    controller = MagicMock()
    hass = _hass(_entry({"office": controller}))

    with pytest.raises(ServiceValidationError, match="Unknown room 'attic'"):
        _resume_control(hass, _call("attic"))

    controller.async_resume_control.assert_not_called()


def test_no_loaded_entry_raises():
    """CC-45: without a loaded hub entry every room is unknown."""
    controller = MagicMock()
    hass = _hass(_entry({"office": controller}, state=ConfigEntryState.NOT_LOADED))

    with pytest.raises(ServiceValidationError):
        _resume_control(hass, _call("office"))

    controller.async_resume_control.assert_not_called()
