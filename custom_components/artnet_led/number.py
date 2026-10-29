"""Number entities of fixtures: pan, tilt, zoom, focus, strobe, amber."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .fixture.entity import FixtureEntity, fixtures_of
from .fixture.fixture import Fixture
from .fixture.fixture_type import COLOR_MIX_MANUAL

# role -> (entity name, icon)
ROLES = {
    "pan": ("Pan", "mdi:pan-horizontal"),
    "tilt": ("Tilt", "mdi:pan-vertical"),
    "zoom": ("Zoom", "mdi:magnify-plus-outline"),
    "focus": ("Fokus", "mdi:image-filter-center-focus"),
    "strobe": ("Strobe", "mdi:flash-outline"),
    "amber": ("Amber", "mdi:circle-half-full"),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    entities = []
    for fixture in fixtures_of(hass):
        ft = fixture.fixture_type
        for role in ROLES:
            if role not in ft.channels:
                continue
            if role == "amber" and ft.effective_color_mix != COLOR_MIX_MANUAL:
                continue  # mixed automatically from the light's colour
            entities.append(FixtureNumber(fixture, role))
    async_add_entities(entities)


class FixtureNumber(FixtureEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(self, fixture: Fixture, role: str):
        name, icon = ROLES[role]
        super().__init__(fixture, role, name)
        self._attr_icon = icon
        ft = fixture.fixture_type
        rng = {"pan": ft.pan_range, "tilt": ft.tilt_range}.get(role)
        spec = ft.channels[role]
        if rng:
            self._scale = rng
            self._attr_native_unit_of_measurement = DEGREE
            self._attr_native_step = round(rng / spec.resolution, 3) if spec.fine else round(rng / 255, 2)
            self._attr_native_step = max(self._attr_native_step, 0.01)
        else:
            self._scale = 100.0
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_native_step = 0.1 if spec.fine else 1
        self._attr_native_min_value = 0
        self._attr_native_max_value = self._scale

    @property
    def native_value(self) -> float:
        return round(getattr(self._fixture.state, self._role) * self._scale, 3)

    async def async_set_native_value(self, value: float) -> None:
        await self._fixture.async_update(**{self._role: max(0.0, min(1.0, value / self._scale))})
