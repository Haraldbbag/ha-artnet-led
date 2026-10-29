"""Colour mixing for multi-emitter fixtures (RGB, RGBW, RGBA, RGBWA ...).

Two strategies:

* ``auto``: a rule of thumb in RGB space. The white part goes to W, the
  orange/yellow part to A, the rest stays on RGB.
* ``calibrated``: uses the measured chromaticity (CIE 1931 x, y) and luminance
  (Y) of every emitter and solves for the mix that hits the target
  chromaticity with the highest light output.

All intensities are floats from 0.0 to 1.0. Pure Python, no numpy needed.
"""
from __future__ import annotations

import colorsys
import itertools
import math
from dataclasses import dataclass

COLOR_ROLES = ("red", "green", "blue", "white", "amber")

# Approximate colour of an amber LED expressed in RGB (green relative to red).
AMBER_GREEN_RATIO = 0.5

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Colour space helpers
# ---------------------------------------------------------------------------

def hs_to_rgb(hue: float, sat: float) -> tuple[float, float, float]:
    """HA hue (0-360) and saturation (0-100) at full value -> RGB 0..1."""
    return colorsys.hsv_to_rgb((hue % 360) / 360.0, max(0.0, min(sat, 100.0)) / 100.0, 1.0)


def kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """Colour temperature -> normalised RGB 0..1 (Tanner Helland approximation)."""
    t = max(1000.0, min(kelvin, 40000.0)) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0
    rgb = [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]
    m = max(rgb) or 1.0
    return tuple(v / m for v in rgb)  # type: ignore[return-value]


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_xy(rgb: tuple[float, float, float]) -> tuple[float, float]:
    """sRGB (0..1, gamma encoded) -> CIE 1931 xy."""
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    s = x + y + z
    if s <= _EPS:
        return 0.3127, 0.3290  # D65
    return x / s, y / s


def hs_to_xy(hue: float, sat: float) -> tuple[float, float]:
    return rgb_to_xy(hs_to_rgb(hue, sat))


def kelvin_to_xy(kelvin: float) -> tuple[float, float]:
    """Point on the Planckian locus (Kim et al. cubic spline, 1667 K - 25000 K)."""
    t = max(1667.0, min(float(kelvin), 25000.0))
    if t <= 4000:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.240390
    if t <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


# ---------------------------------------------------------------------------
# Strategy 1: rule of thumb
# ---------------------------------------------------------------------------

def auto_mix(rgb: tuple[float, float, float], roles: set[str] | frozenset[str]) -> dict[str, float]:
    """Split an RGB colour onto the emitters the fixture has.

    The result is scaled so the strongest emitter is at 1.0 (brightness is
    applied separately).
    """
    r, g, b = (max(0.0, min(1.0, v)) for v in rgb)
    w = a = 0.0
    if "white" in roles:
        w = min(r, g, b)
        r, g, b = r - w, g - w, b - w
    if "amber" in roles:
        a = min(r, g / AMBER_GREEN_RATIO)
        r, g = r - a, g - a * AMBER_GREEN_RATIO
    out = {"red": r, "green": g, "blue": b, "white": w, "amber": a}
    out = {k: v for k, v in out.items() if k in roles}
    return _normalise(out)


# ---------------------------------------------------------------------------
# Strategy 2: calibrated (measured primaries)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Emitter:
    x: float
    y: float
    Y: float  # luminance at 100 %, any unit, only ratios matter

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.x / self.y * self.Y, self.Y, (1.0 - self.x - self.y) / self.y * self.Y)


def _solve2(a11, a12, a21, a22, b1, b2):
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-12:
        return None
    return ((b1 * a22 - a12 * b2) / det, (a11 * b2 - b1 * a21) / det)


def _best_mix(emitters: dict[str, Emitter], target: tuple[float, float]) -> dict[str, float] | None:
    """Mix with the highest luminance that has exactly the target chromaticity.

    Linear programme: maximise sum(a_i * Y_i) subject to
      sum a_i (X_i - kx Y_i) = 0, sum a_i (Z_i - kz Y_i) = 0, 0 <= a_i <= 1
    with kx = x/y, kz = (1-x-y)/y. An optimum sits on a vertex with at most
    two emitters strictly between 0 and 1, so the vertices are enumerated.
    """
    tx, ty = target
    if ty <= _EPS:
        return None
    kx, kz = tx / ty, (1.0 - tx - ty) / ty
    names = list(emitters)
    coef = []
    for n in names:
        X, Y, Z = emitters[n].xyz
        coef.append((X - kx * Y, Z - kz * Y, Y))

    tol = 1e-4 * max(1.0, max(c[2] for c in coef))
    best: tuple[float, list[float]] | None = None
    n = len(names)
    for i, j in itertools.combinations(range(n), 2):
        others = [k for k in range(n) if k not in (i, j)]
        for fixed in itertools.product((0.0, 1.0), repeat=len(others)):
            r1 = -sum(coef[k][0] * v for k, v in zip(others, fixed))
            r2 = -sum(coef[k][1] * v for k, v in zip(others, fixed))
            sol = _solve2(coef[i][0], coef[j][0], coef[i][1], coef[j][1], r1, r2)
            if sol is None:
                continue
            ai, aj = sol
            if not (-1e-9 <= ai <= 1 + 1e-9 and -1e-9 <= aj <= 1 + 1e-9):
                continue
            a = [0.0] * n
            for k, v in zip(others, fixed):
                a[k] = v
            a[i], a[j] = min(1.0, max(0.0, ai)), min(1.0, max(0.0, aj))
            lum = sum(a[k] * coef[k][2] for k in range(n))
            if lum <= tol:
                continue
            if best is None or lum > best[0] + 1e-12:
                best = (lum, a)
    if best is None:
        return None
    return {names[k]: v for k, v in enumerate(best[1])}


def _white_point(emitters: dict[str, Emitter]) -> tuple[float, float]:
    if "white" in emitters:
        return emitters["white"].x, emitters["white"].y
    X = sum(e.xyz[0] for e in emitters.values())
    Y = sum(e.xyz[1] for e in emitters.values())
    Z = sum(e.xyz[2] for e in emitters.values())
    s = X + Y + Z
    return X / s, Y / s


def calibrated_mix(emitters: dict[str, Emitter], target: tuple[float, float]) -> dict[str, float]:
    """Mix for the target xy. Out of gamut targets are desaturated towards the
    fixture's white point until they can be reached (hue is kept)."""
    mix = _best_mix(emitters, target)
    if mix is None:
        wx, wy = _white_point(emitters)
        lo, hi = 0.0, 1.0  # 0 = target, 1 = white point
        found = _best_mix(emitters, (wx, wy))
        for _ in range(24):
            mid = (lo + hi) / 2
            p = (target[0] + (wx - target[0]) * mid, target[1] + (wy - target[1]) * mid)
            m = _best_mix(emitters, p)
            if m is None:
                lo = mid
            else:
                hi, found = mid, m
        mix = found or {k: 1.0 for k in emitters}
    return _normalise(mix)


def mix_xy(emitters: dict[str, Emitter], mix: dict[str, float]) -> tuple[float, float, float]:
    """Resulting x, y, Y of a mix (for tests and diagnostics)."""
    X = sum(emitters[k].xyz[0] * v for k, v in mix.items())
    Y = sum(emitters[k].xyz[1] * v for k, v in mix.items())
    Z = sum(emitters[k].xyz[2] * v for k, v in mix.items())
    s = X + Y + Z
    return (X / s, Y / s, Y) if s > _EPS else (0.0, 0.0, 0.0)


def _normalise(mix: dict[str, float]) -> dict[str, float]:
    m = max(mix.values(), default=0.0)
    if m <= _EPS:
        return {k: 0.0 for k in mix}
    return {k: max(0.0, min(1.0, v / m)) for k, v in mix.items()}
