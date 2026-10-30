"""Tests for fixture types, colour mixing and DMX rendering."""
import asyncio
from unittest.mock import Mock

import pytest
import voluptuous as vol

from custom_components.artnet_led import CONFIG_SCHEMA
from custom_components.artnet_led.fixture import colormix
from custom_components.artnet_led.fixture.colormix import Emitter
from custom_components.artnet_led.fixture.fixture import Fixture, FixtureState
from custom_components.artnet_led.fixture.fixture_type import build_fixture_type

# 19x13 LED moving head, 16 channel mode (from the manual)
ML16 = {
    "pan": 1, "pan_fine": 13, "tilt": 2, "tilt_fine": 14,
    "dimmer": 4, "red": 5, "green": 6, "blue": 7, "white": 8,
    "strobe": 9, "focus": 10,
    "reset": {"channel": 15, "value": 255, "hold": 0.05},
    "static": {3: 0, 11: 0, 12: 0, 16: 0},
    "pan_range": 540, "tilt_range": 270,
}

RGBWA = {"dimmer": 1, "red": 2, "green": 3, "blue": 4, "white": 5, "amber": 6}

# plausible LED primaries for the calibrated tests
EMITTERS = {
    "red": {"x": 0.700, "y": 0.299, "Y": 412},
    "green": {"x": 0.170, "y": 0.720, "Y": 1180},
    "blue": {"x": 0.135, "y": 0.055, "Y": 160},
    "white": {"x": 0.313, "y": 0.329, "Y": 1650},
    "amber": {"x": 0.575, "y": 0.420, "Y": 690},
}


def make(cfg, name="t", **state):
    ft = build_fixture_type(name, cfg)
    f = Fixture(key="h/0/1", name="Test", fixture_type=ft, start=1, universe=0, host="h",
                max_fps=50, state=FixtureState(**state))
    ch = Mock()
    f.attach(ch)
    return f, ch


# ---------------------------------------------------------------- types

def test_width_and_roles():
    ft = build_fixture_type("ml", ML16)
    assert ft.width == 16
    assert ft.color_roles == {"red", "green", "blue", "white"}
    assert ft.channels["pan"].fine == 13 and ft.channels["pan"].max == 65535
    assert ft.channels["dimmer"].max == 255


def test_duplicate_channel_rejected():
    with pytest.raises(vol.Invalid, match="channel 5"):
        build_fixture_type("bad", {"red": 5, "green": 5})


def test_fine_without_coarse_rejected():
    with pytest.raises(vol.Invalid):
        build_fixture_type("bad", {"pan_fine": 2, "red": 1})


def test_calibrated_falls_back_without_emitters():
    ft = build_fixture_type("x", {**RGBWA, "color_mix": "calibrated"})
    assert ft.effective_color_mix == "auto"
    ft = build_fixture_type("x", {**RGBWA, "color_mix": "calibrated", "emitters": EMITTERS})
    assert ft.effective_color_mix == "calibrated"


# ---------------------------------------------------------------- rendering

def test_render_moving_head_16bit_and_static():
    f, ch = make(ML16, on=True, brightness=255, pan=0.5, tilt=1.0, color_mode="hs", hs=(0, 100))
    v = f.dmx_values()
    assert len(v) == 16
    pan16 = v[0] << 8 | v[12]
    assert pan16 == round(0.5 * 65535)
    assert v[1] == 255 and v[13] == 255           # tilt 100 %
    assert v[3] == 255                            # dimmer
    assert (v[4], v[5], v[6], v[7]) == (255, 0, 0, 0)  # pure red
    assert v[2] == 0 and v[10] == 0 and v[15] == 0     # static
    assert v[14] == 0                             # reset idle
    ch.set_values.assert_called()


def test_off_keeps_position():
    f, _ = make(ML16, on=False, pan=0.25, tilt=0.75)
    v = f.dmx_values()
    assert v[3] == 0                               # dimmer closed
    assert (v[0] << 8 | v[12]) == round(0.25 * 65535)
    assert (v[1] << 8 | v[13]) == round(0.75 * 65535)


def test_no_dimmer_scales_colours():
    f, _ = make({"red": 1, "green": 2, "blue": 3}, on=True, brightness=128, color_mode="hs", hs=(120, 100))
    assert f.dmx_values() == [0, 128, 0]


def test_strobe_zero_is_open():
    f, _ = make(ML16, on=True, strobe=0.0)
    assert f.dmx_values()[8] == 0
    f, _ = make(ML16, on=True, strobe=1.0)
    assert f.dmx_values()[8] == 255


def test_min_max_mapping():
    f, _ = make({"zoom": {"channel": 1, "min": 20, "max": 220}}, zoom=0.5)
    assert f.dmx_values() == [120]


def test_gobo():
    f, _ = make({"dimmer": 1, "gobo": {"channel": 2, "values": {"Offen": 0, "Stern": 40}}}, gobo="Stern")
    assert f.dmx_values()[1] == 40
    f.state.gobo = "unbekannt"
    assert f.dmx_values()[1] == 0


# ---------------------------------------------------------------- colour

def test_auto_mix_white_and_amber():
    roles = frozenset(colormix.COLOR_ROLES)
    mix = colormix.auto_mix((1.0, 1.0, 1.0), roles)
    assert mix["white"] == 1.0 and mix["red"] == 0 and mix["blue"] == 0
    warm = colormix.auto_mix(colormix.kelvin_to_rgb(3000), roles)
    assert warm["amber"] > 0.5 and warm["white"] > 0.5 and warm["blue"] == 0
    orange = colormix.auto_mix((1.0, 0.5, 0.0), roles)
    assert orange["amber"] == 1.0 and orange["red"] < 0.01


def test_auto_mix_rgb_only():
    mix = colormix.auto_mix((1.0, 1.0, 1.0), frozenset({"red", "green", "blue"}))
    assert mix == {"red": 1.0, "green": 1.0, "blue": 1.0}


def test_kelvin_to_xy_planck():
    x, y = colormix.kelvin_to_xy(6504)
    assert abs(x - 0.3135) < 0.002 and abs(y - 0.3237) < 0.003
    x, y = colormix.kelvin_to_xy(3000)
    assert abs(x - 0.4369) < 0.002 and abs(y - 0.4041) < 0.002


def _emitters():
    return {k: Emitter(v["x"], v["y"], v["Y"]) for k, v in EMITTERS.items()}


@pytest.mark.parametrize("kelvin", [2700, 3000, 4000, 5600])
def test_calibrated_hits_planck(kelvin):
    em = _emitters()
    target = colormix.kelvin_to_xy(kelvin)
    mix = colormix.calibrated_mix(em, target)
    x, y, _ = colormix.mix_xy(em, mix)
    assert abs(x - target[0]) < 1e-3 and abs(y - target[1]) < 1e-3
    assert max(mix.values()) == pytest.approx(1.0)
    assert all(0 <= v <= 1 for v in mix.values())


def test_calibrated_prefers_bright_mix():
    """For warm white the solution should use W and A rather than RGB."""
    em = _emitters()
    mix = colormix.calibrated_mix(em, colormix.kelvin_to_xy(3000))
    assert mix["white"] > 0.5 and mix["amber"] > 0.3
    assert mix["blue"] < 0.2


def test_calibrated_out_of_gamut_desaturates():
    em = _emitters()
    target = (0.08, 0.85)  # far outside any LED gamut
    mix = colormix.calibrated_mix(em, target)
    x, y, lum = colormix.mix_xy(em, mix)
    assert lum > 0
    assert y > 0.5  # still clearly green


def test_fixture_calibrated_light():
    cfg = {**RGBWA, "color_mix": "calibrated", "emitters": EMITTERS}
    f, _ = make(cfg, on=True, brightness=255, color_mode="color_temp", kelvin=3000)
    v = f.dmx_values()
    assert v[0] == 255
    levels = {r: v[i] / 255 for i, r in enumerate(["red", "green", "blue", "white", "amber"], start=1)}
    x, y, _ = colormix.mix_xy(_emitters(), levels)
    tx, ty = colormix.kelvin_to_xy(3000)
    assert abs(x - tx) < 0.005 and abs(y - ty) < 0.005


def test_manual_mode():
    f, _ = make({**RGBWA, "color_mix": "manual"}, on=True, rgbw=(10, 20, 30, 40), amber=0.5, color_mode="rgbw")
    assert f.dmx_values() == [255, 10, 20, 30, 40, 128]


# ---------------------------------------------------------------- runtime

@pytest.mark.asyncio
async def test_calibration_mode_overrides():
    f, _ = make({**RGBWA}, on=False, brightness=10)
    await f.async_set_calibration("amber")
    assert f.dmx_values() == [255, 0, 0, 0, 0, 255]
    await f.async_set_calibration("off")
    assert f.dmx_values()[0] == 0


@pytest.mark.asyncio
async def test_reset_holds_and_releases():
    f, ch = make(ML16, on=True)
    task = asyncio.create_task(f.async_reset())
    await asyncio.sleep(0.01)
    assert f.dmx_values()[14] == 255 and f.resetting
    await task
    assert f.dmx_values()[14] == 0 and not f.resetting


@pytest.mark.asyncio
async def test_fade_is_16bit_monotonic():
    f, ch = make(ML16, on=True, pan=0.0)
    outputs = []
    ch.set_values.side_effect = lambda vals: outputs.append(vals[0] << 8 | vals[12])
    await f.async_update(transition=0.2, pan=1.0)
    await asyncio.sleep(0.3)
    assert outputs[-1] == 65535
    assert outputs == sorted(outputs)  # never jumps back (bytes faded together)
    assert len(outputs) > 3


@pytest.mark.asyncio
async def test_listener_and_persistence_called():
    f, _ = make(ML16)
    seen, saved = [], []
    f.add_listener(lambda: seen.append(1))
    f.on_state_change = lambda: saved.append(1)
    await f.async_update(tilt=0.3)
    assert seen and saved
    await f.async_set_calibration("red")
    assert len(saved) == 1  # calibration is not persisted


def test_state_roundtrip():
    s = FixtureState(on=True, hs=(10.0, 20.0), pan=0.3)
    s2 = FixtureState.from_dict({**s.__dict__, "hs": [10.0, 20.0], "unknown": 1})
    assert s2 == s


# ---------------------------------------------------------------- yaml

def _yaml(fixtures, types=None):
    return {"artnet_led": {
        "fixture_types": types or {"ml_19x13_16ch": ML16, "spot": RGBWA},
        "nodes": [{"host": "10.0.0.5", "universes": {0: {"fixtures": fixtures}}}],
    }}


def test_config_schema_ok():
    cfg = CONFIG_SCHEMA(_yaml([
        {"channel": 101, "name": "ML links", "fixture_type": "ml_19x13_16ch"},
        {"channel": 117, "name": "ML rechts", "fixture_type": "ml_19x13_16ch"},
        {"channel": 133, "name": "Spot", "fixture_type": "spot"},
    ]))
    node = cfg["artnet_led"]["nodes"][0]
    assert node["node_type"] == "artnet-direct" and node["max_fps"] == 25
    assert cfg["artnet_led"]["fixture_types"]["ml_19x13_16ch"].width == 16


def test_config_schema_overlap():
    with pytest.raises(vol.Invalid, match="overlaps"):
        CONFIG_SCHEMA(_yaml([
            {"channel": 101, "name": "A", "fixture_type": "ml_19x13_16ch"},
            {"channel": 110, "name": "B", "fixture_type": "spot"},
        ]))


def test_config_schema_unknown_type_and_end():
    with pytest.raises(vol.Invalid, match="Unknown fixture_type"):
        CONFIG_SCHEMA(_yaml([{"channel": 1, "name": "A", "fixture_type": "nope"}]))
    with pytest.raises(vol.Invalid, match="beyond 512"):
        CONFIG_SCHEMA(_yaml([{"channel": 500, "name": "A", "fixture_type": "ml_19x13_16ch"}]))


def test_output_correction_on_colours_without_dimmer():
    f, _ = make({**{k: v for k, v in RGBWA.items() if k != "dimmer"}, "output_correction": "quadratic"},
                on=True, brightness=128, color_mode="hs", hs=(0, 100))
    # red at half brightness, quadratic curve -> about a quarter
    assert f.dmx_values()[1] == round(255 * (128 / 255) ** 2)  # red on channel 2


def test_output_correction_on_dimmer_only():
    f, _ = make({**ML16, "output_correction": "quadratic"}, on=True, brightness=128, pan=0.5)
    v = f.dmx_values()
    assert v[3] == round(255 * (128 / 255) ** 2)
    assert (v[0] << 8 | v[12]) == round(0.5 * 65535)  # positions stay linear


def test_default_transition_parsed():
    ft = build_fixture_type("x", {**RGBWA, "transition": 1})
    assert ft.transition == 1.0
    assert build_fixture_type("y", RGBWA).transition == 0.0
