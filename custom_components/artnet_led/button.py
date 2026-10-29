"""Reset button of fixtures."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .fixture.entity import FixtureEntity, fixtures_of
from .fixture.fixture import Fixture


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    async_add_entities(FixtureReset(f) for f in fixtures_of(hass) if f.fixture_type.reset)


class FixtureReset(FixtureEntity, ButtonEntity):
    _attr_icon = "mdi:restart"

    def __init__(self, fixture: Fixture):
        super().__init__(fixture, "reset", "Reset")

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, "resetting": self._fixture.resetting}

    async def async_press(self) -> None:
        # the reset value has to be held for several seconds; don't block the service call
        self.hass.async_create_background_task(self._fixture.async_reset(), f"artnet_led reset {self._fixture.key}")
