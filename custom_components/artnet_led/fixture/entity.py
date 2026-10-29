"""Base entity shared by all fixture entities (one HA device per fixture)."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from ..const import DOMAIN
from .fixture import Fixture


class FixtureEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, fixture: Fixture, role: str, name: str | None):
        self._fixture = fixture
        self._role = role
        self._attr_name = name
        self._attr_unique_id = f"fixture:{fixture.key}/{role}"
        ft = fixture.fixture_type
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"fixture:{fixture.key}")},
            name=fixture.name,
            manufacturer="DMX",
            model=ft.name,
            sw_version=f"Universe {fixture.universe}, DMX {fixture.start}-{fixture.end}",
        )

    @property
    def extra_state_attributes(self):
        return {"fixture_role": self._role}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._fixture.add_listener(self.async_write_ha_state))


def fixtures_of(hass) -> list[Fixture]:
    from ..const import DATA_FIXTURES

    return list(hass.data.get(DOMAIN, {}).get(DATA_FIXTURES, {}).values())
