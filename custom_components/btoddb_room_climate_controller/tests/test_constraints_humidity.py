"""
Tests for per-fan humidity constraints (CC-16/CC-18) and legacy registry cleanup.

Home Assistant is installed in this devcontainer (see ``test_humidity_triggers_
evaluation.py``'s docstring), so ``constraints.py``/``entity.py`` import directly
here. A live ``hass``/event loop is still avoided: ``ConstraintsValidator`` is
driven with lightweight stubs (``_resolve`` monkeypatched to avoid the entity
registry, ``hass.states``/``hass.services`` backed by plain stand-ins), and the
registry cleanup is driven with a tiny stand-in registry rather than a real one.
"""

import asyncio
from unittest.mock import MagicMock, patch

from custom_components.btoddb_room_climate_controller import (
    constraints as constraints_module,
)
from custom_components.btoddb_room_climate_controller import entity as entity_module
from custom_components.btoddb_room_climate_controller.const import (
    KEY_HUMIDITY_HIGH_OFFSET,
    KEY_HUMIDITY_MEDIUM_OFFSET,
    KEY_HUMIDITY_TARGET,
)
from custom_components.btoddb_room_climate_controller.constraints import (
    ConstraintsValidator,
)
from custom_components.btoddb_room_climate_controller.models import (
    Room,
    fan_humidity_high_key,
    fan_humidity_medium_key,
    fan_humidity_target_key,
    room_uid,
)


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
        "fan_entities": ("fan.a", "fan.b"),
        "ac_fan_entity": None,
        "heater_fan_entity": None,
        "ac_power_switch": None,
        "heater_power_switch": None,
        "temperature_sensor": "sensor.office_temp",
        "humidity_sensor": "sensor.office_humidity",
        "power_sensor": None,
        "window_sensors": (),
        "ac_fan_only": False,
        "heater_fan_only": False,
        "limits": {
            "cooling": {"min": 60.0, "max": 90.0},
            "heating": {"min": 50.0, "max": 80.0},
            "fan": {"min": 60.0, "max": 90.0},
        },
        "command_delay": 1.0,
        "power_on_delay": 2.0,
        "heater_setpoint_offset": 2,
        "ac_setpoint_offset": 2,
    }
    defaults.update(overrides)
    return Room(**defaults)


class _StubState:
    def __init__(self, value):
        self.state = str(value)


class _StubStates:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        return None if value is None else _StubState(value)


class _StubServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data, blocking=True):  # noqa: ARG002
        self.calls.append((domain, service, data))


class _StubHass:
    def __init__(self, values):
        self.states = _StubStates(values)
        self.services = _StubServices()


def _make_validator(room, values):
    hass = _StubHass(values)
    entry = MagicMock()
    entry.entry_id = "entry1"
    validator = ConstraintsValidator(hass, entry, room)
    # Deterministic entity ids keyed by the room-uid suffix, bypassing the
    # entity registry entirely (mirrors controller tests' approach).
    validator._resolve = lambda key, domain: f"{domain}.{key}"
    return validator


def test_per_fan_humidity_bounds_clamp_only_the_offending_fan():
    """CC-18: the ≤100% clamp fires only for the fan whose target+high exceeds it."""
    room = _room()
    values = {
        f"number.{fan_humidity_target_key('fan_a')}": 95.0,
        f"number.{fan_humidity_medium_key('fan_a')}": 3.0,
        f"number.{fan_humidity_high_key('fan_a')}": 10.0,  # 95+10 = 105 > 100
        f"number.{fan_humidity_target_key('fan_b')}": 50.0,
        f"number.{fan_humidity_medium_key('fan_b')}": 5.0,
        f"number.{fan_humidity_high_key('fan_b')}": 10.0,  # 50+10 = 60, fine
    }
    validator = _make_validator(room, values)

    with patch.object(constraints_module.persistent_notification, "async_create"):
        asyncio.run(validator._humidity_bounds())

    assert validator.hass.services.calls == [
        (
            "number",
            "set_value",
            {
                "entity_id": f"number.{fan_humidity_high_key('fan_a')}",
                "value": 5.0,
            },
        ),
    ]


def test_per_fan_humidity_order_clamps_only_the_offending_fan():
    """CC-16: the medium<high ordering clamp fires only for the offending fan."""
    room = _room()
    values = {
        f"number.{fan_humidity_medium_key('fan_a')}": 20.0,
        f"number.{fan_humidity_high_key('fan_a')}": 10.0,  # out of order
        f"number.{fan_humidity_medium_key('fan_b')}": 5.0,
        f"number.{fan_humidity_high_key('fan_b')}": 10.0,  # fine
    }
    validator = _make_validator(room, values)

    with patch.object(constraints_module.persistent_notification, "async_create"):
        asyncio.run(validator._humidity_order())

    assert validator.hass.services.calls == [
        (
            "number",
            "set_value",
            {
                "entity_id": f"number.{fan_humidity_high_key('fan_a')}",
                "value": 21.0,
            },
        ),
    ]


def test_vent_fan_is_never_clamped():
    """Vent fan has no humidity offsets (CC-36): it is exempt from both clamp rules."""
    room = _room(
        has_fan=False,
        fan_entities=(),
        has_vent_fan=True,
        vent_fan_entity="switch.office_vent",
    )
    validator = _make_validator(room, {})

    with patch.object(constraints_module.persistent_notification, "async_create"):
        asyncio.run(validator._humidity_order())
        asyncio.run(validator._humidity_bounds())

    assert validator.hass.services.calls == []


# -- legacy shared humidity entity cleanup (CC-28/#77) -----------------------
class _StubRegistryEntry:
    """Minimal ``RegistryEntry`` stand-in: only ``name``/``original_name`` read."""

    def __init__(self, *, name=None, original_name=None):
        self.name = name
        self.original_name = original_name


class _StubEntityRegistry:
    """Minimal entity-registry stand-in: a (domain, unique_id) -> entity_id map."""

    def __init__(self, entries, names=None):
        self._entries = dict(entries)
        names = names or {}
        self.entities = {
            entity_id: _StubRegistryEntry(original_name=names.get(entity_id))
            for entity_id in self._entries.values()
        }
        self.removed: list[str] = []

    def async_get_entity_id(self, domain, _platform, unique_id):
        return self._entries.get((domain, unique_id))

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        for k, v in list(self._entries.items()):
            if v == entity_id:
                del self._entries[k]


def test_cleanup_removes_preseeded_legacy_registry_entries_and_then_no_ops():
    """Legacy shared humidity entities are removed once, then a re-run no-ops."""
    room = _room()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.rooms = {room.key: room}

    legacy_ids = {
        ("number", room_uid("entry1", room.key, KEY_HUMIDITY_TARGET)): (
            "number.office_humidity_target"
        ),
        ("number", room_uid("entry1", room.key, KEY_HUMIDITY_MEDIUM_OFFSET)): (
            "number.office_humidity_medium_offset"
        ),
        ("number", room_uid("entry1", room.key, KEY_HUMIDITY_HIGH_OFFSET)): (
            "number.office_humidity_high_offset"
        ),
    }
    names = {
        "number.office_humidity_target": "Humidity target",
        "number.office_humidity_medium_offset": "Humidity medium offset",
        "number.office_humidity_high_offset": "Humidity high offset",
    }
    stub_registry = _StubEntityRegistry(legacy_ids, names)
    hass = MagicMock()

    with patch.object(entity_module.er, "async_get", return_value=stub_registry):
        entity_module.async_cleanup_legacy_humidity_entities(hass, entry)
        assert sorted(stub_registry.removed) == sorted(legacy_ids.values())

        # Idempotent: nothing left to find, so a second run removes nothing more.
        entity_module.async_cleanup_legacy_humidity_entities(hass, entry)
        assert sorted(stub_registry.removed) == sorted(legacy_ids.values())


def test_cleanup_is_a_noop_when_no_legacy_entities_exist():
    """A room that never had the legacy shared entities is left untouched."""
    room = _room()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.rooms = {room.key: room}
    stub_registry = _StubEntityRegistry({})
    hass = MagicMock()

    with patch.object(entity_module.er, "async_get", return_value=stub_registry):
        entity_module.async_cleanup_legacy_humidity_entities(hass, entry)

    assert stub_registry.removed == []


def test_cleanup_skips_a_unique_id_collision_with_a_current_vent_entity():
    """
    Guards against the ``room_uid`` collision documented on the cleanup fn.

    ``room_uid(rid, "office_vent_fan", "humidity_target")`` collides with
    ``room_uid(rid, "office", "vent_fan_humidity_target")`` — the *current*
    vent humidity-target entity for a differently-keyed room. The legacy
    cleanup must only remove a registry entry whose name actually matches one
    of the pre-#77 shared humidity entities, so it must leave this one alone.
    """
    room = _room(key="office_vent_fan")
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data.rooms = {room.key: room}

    colliding_uid = room_uid("entry1", room.key, KEY_HUMIDITY_TARGET)
    assert colliding_uid == room_uid("entry1", "office", "vent_fan_humidity_target")
    entity_id = "number.office_vent_fan_humidity_target"
    legacy_ids = {("number", colliding_uid): entity_id}
    names = {entity_id: "Vent fan humidity target"}
    stub_registry = _StubEntityRegistry(legacy_ids, names)
    hass = MagicMock()

    with patch.object(entity_module.er, "async_get", return_value=stub_registry):
        entity_module.async_cleanup_legacy_humidity_entities(hass, entry)

    assert stub_registry.removed == []
