"""Runtime state of one fixture and its DMX output.

A fixture owns one contiguous pyartnet channel (8 bit per slot) covering all
of its DMX addresses. All logical values (pan, tilt, colours ...) live here;
the Home Assistant entities only read and change them. 16 bit values are split
onto coarse/fine slots here, which also keeps fades correct (pyartnet would
fade the two bytes independently).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from . import colormix
from .fixture_type import (
    COLOR_MIX_CALIBRATED,
    COLOR_MIX_MANUAL,
    FixtureType,
)

log = logging.getLogger(__name__)

CALIBRATION_OFF = "off"
CALIBRATION_ALL = "all"


@dataclass
class FixtureState:
    """Everything that is restored after a restart."""
    on: bool = False
    brightness: int = 255            # 0..255 like HA
    color_mode: str = "color_temp"   # hs | color_temp | rgbw | rgb
    hs: tuple[float, float] = (0.0, 0.0)
    kelvin: int = 3200
    rgbw: tuple[int, int, int, int] = (255, 255, 255, 255)
    amber: float = 0.0               # 0..1, manual mode only
    pan: float = 0.5                 # 0..1
    tilt: float = 0.5
    zoom: float = 0.0
    focus: float = 0.0
    strobe: float = 0.0              # 0 = open
    gobo: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixtureState":
        state = cls()
        for key, value in (data or {}).items():
            if not hasattr(state, key):
                continue
            if key in ("hs", "rgbw") and isinstance(value, list):
                value = tuple(value)
            setattr(state, key, value)
        return state


@dataclass
class Fixture:
    key: str                 # stable id, e.g. "10.0.0.5/0/101"
    name: str
    fixture_type: FixtureType
    start: int               # absolute DMX start address
    universe: int
    host: str
    max_fps: int = 25
    state: FixtureState = field(default_factory=FixtureState)

    def __post_init__(self):
        self._channel = None
        self._listeners: list[Callable[[], None]] = []
        self._fade_task: asyncio.Task | None = None
        self._resetting = False
        self.calibration = CALIBRATION_OFF
        self._current: dict[int, float] | None = None   # slot -> value currently output
        self.on_state_change: Callable[[], None] | None = None  # used for persistence

    # ------------------------------------------------------------------ setup

    @property
    def end(self) -> int:
        return self.start + self.fixture_type.width - 1

    def attach(self, channel) -> None:
        """Attach the pyartnet channel and output the (restored) state."""
        self._channel = channel
        self._output(self.render())

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _notify(self, persist: bool = True) -> None:
        for cb in list(self._listeners):
            cb()
        if persist and self.on_state_change:
            self.on_state_change()

    # ---------------------------------------------------------------- mixing

    def color_levels(self) -> dict[str, float]:
        """Emitter levels 0..1 before brightness, for the current colour."""
        ft = self.fixture_type
        roles = ft.color_roles
        if not roles:
            return {}
        if self.calibration != CALIBRATION_OFF:
            return {r: 1.0 if self.calibration in (r, CALIBRATION_ALL) else 0.0 for r in roles}

        s = self.state
        mix_mode = ft.effective_color_mix
        if mix_mode == COLOR_MIX_MANUAL or s.color_mode in ("rgbw", "rgb"):
            r, g, b, w = (v / 255.0 for v in s.rgbw)
            out = {"red": r, "green": g, "blue": b, "white": w, "amber": s.amber}
            return {k: v for k, v in out.items() if k in roles}

        if mix_mode == COLOR_MIX_CALIBRATED:
            target = colormix.kelvin_to_xy(s.kelvin) if s.color_mode == "color_temp" \
                else colormix.hs_to_xy(*s.hs)
            emitters = {r: ft.emitters[r] for r in roles}
            return colormix.calibrated_mix(emitters, target)

        rgb = colormix.kelvin_to_rgb(s.kelvin) if s.color_mode == "color_temp" \
            else colormix.hs_to_rgb(*s.hs)
        return colormix.auto_mix(rgb, roles)

    # ---------------------------------------------------------------- render

    def logical_values(self) -> dict[str, float]:
        """role -> 0..1 output level (after brightness / on-off)."""
        ft = self.fixture_type
        s = self.state
        out: dict[str, float] = {}
        level = (s.brightness / 255.0) if s.on else 0.0
        calibrating = self.calibration != CALIBRATION_OFF
        if calibrating:
            level = 1.0

        colors = self.color_levels()
        if "dimmer" in ft.channels:
            out["dimmer"] = level
            out.update(colors)
        else:
            out.update({k: v * level for k, v in colors.items()})

        out["pan"] = s.pan
        out["tilt"] = s.tilt
        out["zoom"] = s.zoom
        out["focus"] = s.focus
        out["strobe"] = 0.0 if calibrating else s.strobe
        return {k: v for k, v in out.items() if k in ft.channels}

    def render(self) -> dict[int, float]:
        """Relative slot (1-based) -> DMX value. 16 bit values span two slots."""
        ft = self.fixture_type
        slots: dict[int, float] = {i: 0.0 for i in range(1, ft.width + 1)}
        for role, level in self.logical_values().items():
            spec = ft.channels[role]
            raw = spec.min + max(0.0, min(1.0, level)) * (spec.max - spec.min)
            if spec.fine:
                slots[spec.channel] = raw / 256.0   # fractional coarse, split on output
                slots[spec.fine] = -1.0              # marker: filled from coarse
            else:
                slots[spec.channel] = raw
        if ft.gobo:
            slots[ft.gobo.channel] = ft.gobo.options.get(self.state.gobo, next(iter(ft.gobo.options.values())))
        if ft.reset:
            slots[ft.reset.channel] = ft.reset.value if self._resetting else ft.reset.idle
        for ch, value in ft.static.items():
            slots[ch] = value
        return slots

    def _to_bytes(self, slots: dict[int, float]) -> list[int]:
        ft = self.fixture_type
        values = [0] * ft.width
        fine_of = {spec.channel: spec.fine for spec in ft.channels.values() if spec.fine}
        for ch, val in slots.items():
            if val < 0:
                continue
            if ch in fine_of:
                raw16 = max(0, min(65535, round(val * 256.0)))
                values[ch - 1] = raw16 >> 8
                values[fine_of[ch] - 1] = raw16 & 0xFF
            else:
                values[ch - 1] = max(0, min(255, round(val)))
        return values

    def dmx_values(self) -> list[int]:
        return self._to_bytes(self.render())

    def _output(self, slots: dict[int, float]) -> None:
        self._current = slots
        if self._channel is not None:
            self._channel.set_values(self._to_bytes(slots))

    # ------------------------------------------------------------ changes

    async def async_update(self, transition: float = 0.0, **changes) -> None:
        """Change state values and output them, optionally fading."""
        for key, value in changes.items():
            if not hasattr(self.state, key):
                raise AttributeError(key)
            setattr(self.state, key, value)
        self._apply(transition)
        self._notify()

    def _apply(self, transition: float = 0.0) -> None:
        target = self.render()
        if self._fade_task and not self._fade_task.done():
            self._fade_task.cancel()
        if transition and transition > 0 and self._current is not None:
            self._fade_task = asyncio.get_running_loop().create_task(self._fade(dict(self._current), target, transition))
        else:
            self._output(target)

    async def _fade(self, start: dict[int, float], target: dict[int, float], duration: float) -> None:
        step = 1.0 / max(1, self.max_fps)
        t0 = time.monotonic()
        while (p := (time.monotonic() - t0) / duration) < 1.0:
            frame = {}
            for ch, end in target.items():
                begin = start.get(ch, end)
                frame[ch] = end if end < 0 or begin < 0 else begin + (end - begin) * p
            self._output(frame)
            await asyncio.sleep(step)
        self._output(target)

    async def async_set_calibration(self, mode: str) -> None:
        self.calibration = mode
        self._apply(0)
        self._notify(persist=False)

    async def async_reset(self) -> None:
        """Hold the reset value for the configured time, then go back to idle."""
        ft = self.fixture_type
        if not ft.reset or self._resetting:
            return
        self._resetting = True
        self._apply(0)
        self._notify(persist=False)
        try:
            await asyncio.sleep(ft.reset.hold)
        finally:
            self._resetting = False
            self._apply(0)
            self._notify(persist=False)

    @property
    def resetting(self) -> bool:
        return self._resetting

    def stop(self) -> None:
        if self._fade_task and not self._fade_task.done():
            self._fade_task.cancel()

    def state_dict(self) -> dict[str, Any]:
        return asdict(self.state)
