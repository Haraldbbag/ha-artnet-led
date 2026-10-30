"""Light entity of a fixture: brightness and colour."""
from __future__ import annotations

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)

from .entity import FixtureEntity
from .fixture import Fixture
from .fixture_type import COLOR_MIX_MANUAL, FixtureType


def light_color_modes(ft: FixtureType) -> set[ColorMode]:
    """Colour modes offered for a fixture type; empty = no light entity."""
    if ft.has_rgb:
        if ft.effective_color_mix == COLOR_MIX_MANUAL:
            return {ColorMode.RGBW} if "white" in ft.channels else {ColorMode.RGB}
        return {ColorMode.HS, ColorMode.COLOR_TEMP}
    if {"white", "amber"} <= ft.channels.keys():
        return {ColorMode.COLOR_TEMP}
    if ft.color_roles or "dimmer" in ft.channels:
        return {ColorMode.BRIGHTNESS}
    return set()


class FixtureLight(FixtureEntity, LightEntity):
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(self, fixture: Fixture):
        super().__init__(fixture, "light", None)  # named like the device
        ft = fixture.fixture_type
        self._attr_supported_color_modes = light_color_modes(ft)
        self._attr_min_color_temp_kelvin = ft.min_temp
        self._attr_max_color_temp_kelvin = ft.max_temp

    @property
    def extra_state_attributes(self):
        f = self._fixture
        return {**super().extra_state_attributes, "fixture_type": f.fixture_type.name,
                "dmx_start": f.start, "universe": f.universe,
                "color_mix": f.fixture_type.effective_color_mix}

    @property
    def is_on(self) -> bool:
        return self._fixture.state.on

    @property
    def brightness(self) -> int:
        return self._fixture.state.brightness

    @property
    def color_mode(self) -> ColorMode:
        modes = self._attr_supported_color_modes
        mode = self._fixture.state.color_mode
        if len(modes) == 1:
            return next(iter(modes))
        return ColorMode.HS if mode == "hs" else ColorMode.COLOR_TEMP

    @property
    def hs_color(self):
        return tuple(self._fixture.state.hs)

    @property
    def color_temp_kelvin(self) -> int:
        return self._fixture.state.kelvin

    @property
    def rgbw_color(self):
        return tuple(self._fixture.state.rgbw)

    @property
    def rgb_color(self):
        return tuple(self._fixture.state.rgbw[:3])

    async def async_turn_on(self, **kwargs) -> None:
        changes: dict = {"on": True}
        if ATTR_BRIGHTNESS in kwargs:
            if kwargs[ATTR_BRIGHTNESS] <= 0:
                await self.async_turn_off(**kwargs)
                return
            changes["brightness"] = kwargs[ATTR_BRIGHTNESS]
        if ATTR_HS_COLOR in kwargs:
            changes["hs"] = tuple(kwargs[ATTR_HS_COLOR])
            changes["color_mode"] = "hs"
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            ft = self._fixture.fixture_type
            changes["kelvin"] = int(max(ft.min_temp, min(ft.max_temp, kwargs[ATTR_COLOR_TEMP_KELVIN])))
            changes["color_mode"] = "color_temp"
        if ATTR_RGBW_COLOR in kwargs:
            changes["rgbw"] = tuple(kwargs[ATTR_RGBW_COLOR])
            changes["color_mode"] = "rgbw"
        if ATTR_RGB_COLOR in kwargs:
            changes["rgbw"] = (*kwargs[ATTR_RGB_COLOR], 0)
            changes["color_mode"] = "rgb"
        await self._fixture.async_update(
            transition=kwargs.get(ATTR_TRANSITION, self._fixture.fixture_type.transition), **changes)

    async def async_turn_off(self, **kwargs) -> None:
        # only the light goes dark; pan/tilt/zoom stay where they are
        await self._fixture.async_update(
            transition=kwargs.get(ATTR_TRANSITION, self._fixture.fixture_type.transition), on=False)
