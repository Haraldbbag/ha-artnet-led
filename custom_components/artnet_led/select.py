"""Select entities of fixtures: gobo and the calibration mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .fixture.entity import FixtureEntity, fixtures_of
from .fixture.fixture import CALIBRATION_ALL, CALIBRATION_OFF, Fixture

CALIBRATION_LABELS = {
    CALIBRATION_OFF: "Aus",
    "red": "Rot",
    "green": "Grün",
    "blue": "Blau",
    "white": "Weiß",
    "amber": "Amber",
    CALIBRATION_ALL: "Alle",
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    entities = []
    for fixture in fixtures_of(hass):
        ft = fixture.fixture_type
        if ft.gobo:
            entities.append(FixtureGobo(fixture))
        if ft.color_roles:
            entities.append(FixtureCalibration(fixture))
    async_add_entities(entities)


class FixtureGobo(FixtureEntity, SelectEntity):
    _attr_icon = "mdi:image-filter-vintage"

    def __init__(self, fixture: Fixture):
        super().__init__(fixture, "gobo", "Gobo")
        self._attr_options = list(fixture.fixture_type.gobo.options)

    @property
    def current_option(self) -> str:
        current = self._fixture.state.gobo
        return current if current in self._attr_options else self._attr_options[0]

    async def async_select_option(self, option: str) -> None:
        await self._fixture.async_update(gobo=option)


class FixtureCalibration(FixtureEntity, SelectEntity):
    """Switches single emitters to 100 % for measuring them (not restored)."""
    _attr_icon = "mdi:palette-swatch-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, fixture: Fixture):
        super().__init__(fixture, "calibration", "Kalibrierung")
        keys = [CALIBRATION_OFF, *sorted(fixture.fixture_type.color_roles,
                                        key=list(CALIBRATION_LABELS).index), CALIBRATION_ALL]
        self._labels = {k: CALIBRATION_LABELS[k] for k in keys}
        self._attr_options = list(self._labels.values())

    @property
    def current_option(self) -> str:
        return self._labels.get(self._fixture.calibration, self._labels[CALIBRATION_OFF])

    async def async_select_option(self, option: str) -> None:
        key = next(k for k, v in self._labels.items() if v == option)
        await self._fixture.async_set_calibration(key)
