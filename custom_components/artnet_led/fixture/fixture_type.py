"""Fixture types: a DMX personality described once, used by many fixtures.

Example::

    ml_19x13_16ch:
      pan: 1
      pan_fine: 13
      tilt: 2
      tilt_fine: 14
      dimmer: 4
      red: 5
      green: 6
      blue: 7
      white: 8
      strobe: 9
      focus: 10
      reset: {channel: 15, value: 255, hold: 6}
      static: {3: 0, 11: 0, 12: 0, 16: 0}
      pan_range: 540
      tilt_range: 270

Channel numbers are relative to the fixture's start address (1 = start).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import voluptuous as vol

from .colormix import COLOR_ROLES, Emitter

# Roles that hold a single continuous value (optionally 16 bit with *_fine).
VALUE_ROLES = ("dimmer", "red", "green", "blue", "white", "amber",
               "pan", "tilt", "strobe", "zoom", "focus")
FINE_ROLES = ("dimmer", "pan", "tilt", "zoom", "focus")

COLOR_MIX_AUTO = "auto"
COLOR_MIX_CALIBRATED = "calibrated"
COLOR_MIX_MANUAL = "manual"
COLOR_MIXES = (COLOR_MIX_AUTO, COLOR_MIX_CALIBRATED, COLOR_MIX_MANUAL)

# exponent of the dimming curve, same names as the light: platform
OUTPUT_CORRECTIONS = {"linear": 1, "quadratic": 2, "cubic": 3, "quadruple": 4}

DMX_CHANNEL = vol.All(vol.Coerce(int), vol.Range(min=1, max=512))
DMX_VALUE = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))


@dataclass(frozen=True)
class ChannelSpec:
    channel: int               # coarse channel, relative (1 = start address)
    fine: int | None = None    # fine channel for 16 bit, relative
    min: int = 0               # DMX value at 0 %
    max: int = 255             # DMX value at 100 % (16 bit: 0..65535)

    @property
    def resolution(self) -> int:
        return 65535 if self.fine else 255


@dataclass(frozen=True)
class ResetSpec:
    channel: int
    value: int = 255
    hold: float = 6.0
    idle: int = 0


@dataclass(frozen=True)
class GoboSpec:
    channel: int
    options: dict[str, int]


@dataclass(frozen=True)
class FixtureType:
    name: str
    channels: dict[str, ChannelSpec]
    static: dict[int, int] = field(default_factory=dict)
    reset: ResetSpec | None = None
    gobo: GoboSpec | None = None
    pan_range: float | None = None
    tilt_range: float | None = None
    color_mix: str = COLOR_MIX_AUTO
    emitters: dict[str, Emitter] = field(default_factory=dict)
    min_temp: int = 2700
    max_temp: int = 6500
    output_correction: str = "linear"   # dimming curve for dimmer / colour levels
    transition: float = 0.0              # default fade of the light in seconds

    @property
    def width(self) -> int:
        used = list(self.static)
        for spec in self.channels.values():
            used.append(spec.channel)
            if spec.fine:
                used.append(spec.fine)
        if self.reset:
            used.append(self.reset.channel)
        if self.gobo:
            used.append(self.gobo.channel)
        return max(used)

    @property
    def color_roles(self) -> frozenset[str]:
        return frozenset(r for r in COLOR_ROLES if r in self.channels)

    @property
    def has_rgb(self) -> bool:
        return bool({"red", "green", "blue"} & self.channels.keys())

    @property
    def effective_color_mix(self) -> str:
        """Calibrated mixing needs an emitter for every colour channel."""
        if self.color_mix == COLOR_MIX_CALIBRATED and not self.color_roles <= self.emitters.keys():
            return COLOR_MIX_AUTO
        return self.color_mix


def _channel_spec(value):
    if isinstance(value, dict):
        return CHANNEL_DICT_SCHEMA(value)
    return {"channel": DMX_CHANNEL(value)}


CHANNEL_DICT_SCHEMA = vol.Schema({
    vol.Required("channel"): DMX_CHANNEL,
    vol.Optional("min"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    vol.Optional("max"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
})

EMITTER_SCHEMA = vol.Schema({
    vol.Required("x"): vol.All(vol.Coerce(float), vol.Range(min=0.0001, max=0.9999)),
    vol.Required("y"): vol.All(vol.Coerce(float), vol.Range(min=0.0001, max=0.9999)),
    vol.Required("Y"): vol.All(vol.Coerce(float), vol.Range(min=0.0, min_included=False)),
})


def _kelvin(value) -> int:
    s = str(value).strip().upper().rstrip("K")
    return vol.All(vol.Coerce(int), vol.Range(min=1000, max=20000))(s)


FIXTURE_TYPE_SCHEMA = vol.Schema({
    **{vol.Optional(role): _channel_spec for role in VALUE_ROLES},
    **{vol.Optional(f"{role}_fine"): DMX_CHANNEL for role in FINE_ROLES},
    vol.Optional("reset"): vol.Any(
        vol.All(DMX_CHANNEL, lambda c: {"channel": c}),
        vol.Schema({
            vol.Required("channel"): DMX_CHANNEL,
            vol.Optional("value", default=255): DMX_VALUE,
            vol.Optional("hold", default=6.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
            vol.Optional("idle", default=0): DMX_VALUE,
        }),
    ),
    vol.Optional("gobo"): vol.Schema({
        vol.Required("channel"): DMX_CHANNEL,
        vol.Required("values"): vol.All(
            {vol.Coerce(str): DMX_VALUE}, vol.Length(min=1)
        ),
    }),
    vol.Optional("static", default={}): {DMX_CHANNEL: DMX_VALUE},
    vol.Optional("pan_range"): vol.All(vol.Coerce(float), vol.Range(min=1, max=1080)),
    vol.Optional("tilt_range"): vol.All(vol.Coerce(float), vol.Range(min=1, max=1080)),
    vol.Optional("color_mix", default=COLOR_MIX_AUTO): vol.In(COLOR_MIXES),
    vol.Optional("emitters", default={}): {vol.In(COLOR_ROLES): EMITTER_SCHEMA},
    vol.Optional("min_temp", default=2700): _kelvin,
    vol.Optional("max_temp", default=6500): _kelvin,
    vol.Optional("output_correction", default="linear"): vol.In(OUTPUT_CORRECTIONS),
    vol.Optional("transition", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=999)),
})


def build_fixture_type(name: str, cfg: dict) -> FixtureType:
    """Turn a validated config dict into a FixtureType and check it for conflicts."""
    cfg = FIXTURE_TYPE_SCHEMA(cfg)
    channels: dict[str, ChannelSpec] = {}
    for role in VALUE_ROLES:
        if role not in cfg:
            continue
        raw = cfg[role]
        fine = cfg.get(f"{role}_fine")
        resolution = 65535 if fine else 255
        lo = raw.get("min", 0)
        hi = raw.get("max", resolution)
        if lo > resolution or hi > resolution:
            raise vol.Invalid(f"{name}: {role} min/max must be <= {resolution}")
        channels[role] = ChannelSpec(channel=raw["channel"], fine=fine, min=lo, max=hi)
    for role in FINE_ROLES:
        if f"{role}_fine" in cfg and role not in cfg:
            raise vol.Invalid(f"{name}: {role}_fine given without {role}")

    reset = ResetSpec(**cfg["reset"]) if "reset" in cfg else None
    gobo = GoboSpec(channel=cfg["gobo"]["channel"], options=dict(cfg["gobo"]["values"])) if "gobo" in cfg else None
    emitters = {k: Emitter(v["x"], v["y"], v["Y"]) for k, v in cfg["emitters"].items()}
    if cfg["min_temp"] >= cfg["max_temp"]:
        raise vol.Invalid(f"{name}: min_temp must be lower than max_temp")

    ft = FixtureType(
        name=name, channels=channels, static=dict(cfg["static"]), reset=reset, gobo=gobo,
        pan_range=cfg.get("pan_range"), tilt_range=cfg.get("tilt_range"),
        color_mix=cfg["color_mix"], emitters=emitters,
        min_temp=cfg["min_temp"], max_temp=cfg["max_temp"],
        output_correction=cfg["output_correction"], transition=cfg["transition"],
    )

    # every DMX channel may be used only once
    seen: dict[int, str] = {}

    def claim(ch: int, what: str):
        if ch in seen:
            raise vol.Invalid(f"{name}: channel {ch} used for both {seen[ch]} and {what}")
        seen[ch] = what

    for role, spec in channels.items():
        claim(spec.channel, role)
        if spec.fine:
            claim(spec.fine, f"{role}_fine")
    if reset:
        claim(reset.channel, "reset")
    if gobo:
        claim(gobo.channel, "gobo")
    for ch in ft.static:
        claim(ch, "static")
    if not seen:
        raise vol.Invalid(f"{name}: fixture type has no channels")
    return ft
