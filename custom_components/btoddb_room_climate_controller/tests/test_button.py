"""
Tests for the per-room "Resume control" button (CC-45).

``button.py`` imports directly (Home Assistant is installed in this
devcontainer); the config entry, hub and rooms are ``SimpleNamespace``
stand-ins and ``async_add_entities`` is a recorder.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.btoddb_room_climate_controller import button
from custom_components.btoddb_room_climate_controller.const import (
    DOMAIN,
    KEY_RESUME_CONTROL,
)
from custom_components.btoddb_room_climate_controller.models import room_uid


def _room(key, *, has_ac=False, has_heater=False, has_vent_fan=False, has_fan=False):
    return SimpleNamespace(
        room_id=f"sub_{key}",
        key=key,
        label=key.title(),
        has_ac=has_ac,
        has_heater=has_heater,
        has_vent_fan=has_vent_fan,
        has_fan=has_fan,
    )


def _entry(rooms=(), controllers=None):
    return SimpleNamespace(
        entry_id="entry1",
        runtime_data=SimpleNamespace(
            rooms={room.key: room for room in rooms},
            controllers=controllers or {},
        ),
    )


def test_press_resumes_control_via_controller():
    """CC-45: pressing calls the room's controller with source "button"."""
    room = _room("office", has_ac=True)
    controller = MagicMock()
    entity = button.ResumeControlButton(
        _entry([room], {"office": controller, "den": MagicMock()}), room
    )

    asyncio.run(entity.async_press())

    controller.async_resume_control.assert_called_once_with("button")


def test_press_without_controller_is_harmless(caplog):
    """CC-45: a press before the controller starts only logs a warning."""
    room = _room("office", has_ac=True)
    entity = button.ResumeControlButton(_entry([room]), room)

    with caplog.at_level(logging.WARNING, logger=button.__name__):
        asyncio.run(entity.async_press())

    assert any("[room=office]" in r.message for r in caplog.records)


def test_button_identity():
    """The button uses the deterministic per-room unique_id and device."""
    room = _room("office", has_heater=True)
    entity = button.ResumeControlButton(_entry([room]), room)

    assert entity.unique_id == room_uid("entry1", "office", KEY_RESUME_CONTROL)
    assert entity.name == "Resume control"
    assert entity.icon == "mdi:play-circle-outline"
    assert entity.device_info["identifiers"] == {(DOMAIN, "entry1_room_office")}


def test_setup_creates_buttons_only_for_holdable_rooms():
    """CC-45: A/C, heater, vent and fan rooms get a button; a bare room doesn't."""
    rooms = [
        _room("ac_room", has_ac=True),
        _room("heat_room", has_heater=True),
        _room("vent_room", has_vent_fan=True),
        _room("fan_room", has_fan=True),
        _room("bare_room"),
    ]
    added: list[tuple[list, dict]] = []

    def add_entities(entities, **kwargs):
        added.append((entities, kwargs))

    asyncio.run(button.async_setup_entry(MagicMock(), _entry(rooms), add_entities))

    assert [(e[0]._room.key, kw) for e, kw in added] == [
        ("ac_room", {"config_subentry_id": "sub_ac_room"}),
        ("heat_room", {"config_subentry_id": "sub_heat_room"}),
        ("vent_room", {"config_subentry_id": "sub_vent_room"}),
        ("fan_room", {"config_subentry_id": "sub_fan_room"}),
    ]
    assert all(len(entities) == 1 for entities, _kw in added)
    assert all(
        isinstance(entities[0], button.ResumeControlButton) for entities, _kw in added
    )
