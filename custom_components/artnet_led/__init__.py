"""ARTNET LED

Besides the classic ``light:`` platform, this integration supports fixtures
(moving heads, RGBWA spots ...) configured under a top level ``artnet_led:``
key. Each fixture becomes one Home Assistant device with a light, numbers
(pan, tilt, zoom, focus, strobe), selects (gobo, calibration) and a reset
button.
"""
from __future__ import annotations

import logging
from pathlib import Path

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CARD_URL,
    CONF_FIXTURE_TYPE,
    CONF_FIXTURE_TYPES,
    CONF_FIXTURES,
    CONF_NODES,
    CONF_UNIVERSES,
    DATA_FIXTURES,
    DATA_STORE,
    DATA_YAML,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)

log = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.NUMBER, Platform.SELECT, Platform.BUTTON]


def _fixture_types(value):
    from .fixture.fixture_type import build_fixture_type

    value = vol.Schema({cv.slug: dict})(value)
    return {name: build_fixture_type(name, cfg) for name, cfg in value.items()}


FIXTURE_SCHEMA = vol.Schema({
    vol.Required("channel"): vol.All(vol.Coerce(int), vol.Range(min=1, max=512)),
    vol.Required("name"): cv.string,
    vol.Required(CONF_FIXTURE_TYPE): cv.slug,
})


def _node_schema():
    from .nodes import NODE_TYPES

    return vol.Schema({
        vol.Required("host"): cv.string,
        vol.Optional("port"): cv.port,
        vol.Optional("host_override", default=""): cv.string,
        vol.Optional("port_override"): cv.port,
        vol.Optional("node_type", default="artnet-direct"): vol.In(NODE_TYPES),
        vol.Optional("max_fps", default=25): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
        vol.Optional("refresh_every", default=120): vol.All(vol.Coerce(float), vol.Range(min=0, max=9999)),
        vol.Required(CONF_UNIVERSES): {
            vol.All(vol.Coerce(int), vol.Range(min=0, max=32767)): {
                vol.Required(CONF_FIXTURES): vol.All(cv.ensure_list, [FIXTURE_SCHEMA]),
            }
        },
    })


def _check_references(cfg):
    types = cfg[CONF_FIXTURE_TYPES]
    for node in cfg[CONF_NODES]:
        for universe, ucfg in node[CONF_UNIVERSES].items():
            ranges = []
            for fx in ucfg[CONF_FIXTURES]:
                ft = types.get(fx[CONF_FIXTURE_TYPE])
                if ft is None:
                    raise vol.Invalid(f"Unknown fixture_type '{fx[CONF_FIXTURE_TYPE]}' for '{fx['name']}'")
                start, end = fx["channel"], fx["channel"] + ft.width - 1
                if end > 512:
                    raise vol.Invalid(f"'{fx['name']}' ends at channel {end}, beyond 512")
                for other_name, o_start, o_end in ranges:
                    if start <= o_end and o_start <= end:
                        raise vol.Invalid(
                            f"'{fx['name']}' ({start}-{end}) overlaps '{other_name}' ({o_start}-{o_end}) "
                            f"in universe {universe} of {node['host']}")
                ranges.append((fx["name"], start, end))
    return cfg


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            vol.Schema({
                vol.Required(CONF_FIXTURE_TYPES): _fixture_types,
                vol.Required(CONF_NODES): vol.All(cv.ensure_list, [lambda v: _node_schema()(v)]),
            }),
            _check_references,
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    if DOMAIN not in config:
        return True

    hass.data.setdefault(DOMAIN, {})[DATA_YAML] = config[DOMAIN]
    await _async_register_card(hass)

    hass.async_create_task(
        hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data={})
    )
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        log.debug("Frontend not loaded, artnet fixture card not registered")
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        path = Path(__file__).parent / "frontend" / "artnet-fixture-card.js"
        await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
        # version query so browsers fetch the new card after an update
        mtime = int((await hass.async_add_executor_job(path.stat)).st_mtime)
        add_extra_js_url(hass, f"{CARD_URL}?v={mtime}")
    except Exception:  # noqa: BLE001 - the card is optional, the fixtures still work
        log.exception("Could not register the artnet fixture card")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .fixture.fixture import Fixture, FixtureState
    from .nodes import async_get_node

    data = hass.data.setdefault(DOMAIN, {})
    cfg = data.get(DATA_YAML)
    fixtures: dict[str, Fixture] = {}
    data[DATA_FIXTURES] = fixtures
    if cfg is None:
        log.warning("artnet_led: no 'artnet_led:' block in configuration.yaml, no fixtures created")
        return True

    store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data[DATA_STORE] = store
    saved: dict = await store.async_load() or {}

    def save_later():
        store.async_delay_save(lambda: {k: f.state_dict() for k, f in fixtures.items()}, 2.0)

    for node_cfg in cfg[CONF_NODES]:
        node = await async_get_node(
            hass, node_cfg["node_type"], node_cfg["host"], node_cfg.get("port"),
            node_cfg.get("host_override"), node_cfg.get("port_override"),
            node_cfg["max_fps"], node_cfg["refresh_every"],
        )
        for universe_nr, ucfg in node_cfg[CONF_UNIVERSES].items():
            universe = _get_universe(node, universe_nr)
            for fx_cfg in ucfg[CONF_FIXTURES]:
                ft = cfg[CONF_FIXTURE_TYPES][fx_cfg[CONF_FIXTURE_TYPE]]
                key = f"{node_cfg['host']}/{universe_nr}/{fx_cfg['channel']}"
                fixture = Fixture(
                    key=key, name=fx_cfg["name"], fixture_type=ft, start=fx_cfg["channel"],
                    universe=universe_nr, host=node_cfg["host"], max_fps=node_cfg["max_fps"],
                    state=FixtureState.from_dict(saved.get(key, {})),
                )
                fixture.attach(_get_channel(universe, fixture))
                fixture.on_state_change = save_later
                fixtures[key] = fixture

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _get_universe(node, nr: int):
    from pyartnet.errors import UniverseNotFoundError

    try:
        return node.get_universe(nr)
    except UniverseNotFoundError:
        universe = node.add_universe(nr)
        universe._artnet_led_by_fixtures = True  # light: platform may still set its correction
        return universe


def _get_channel(universe, fixture):
    """One 8 bit channel spanning the whole fixture. Reused on reload."""
    import pyartnet
    from pyartnet.errors import ChannelNotFoundError

    name = f"fixture:{fixture.start}"
    try:
        channel = universe.get_channel(name)
        if channel._width == fixture.fixture_type.width:
            return channel
        raise ValueError(f"DMX layout of '{fixture.name}' changed, restart Home Assistant")
    except ChannelNotFoundError:
        pass
    channel = universe.add_channel(start=fixture.start, width=fixture.fixture_type.width,
                                   channel_name=name, byte_size=1)
    # pan/tilt/colour values must never be bent by a universe output correction
    channel.set_output_correction(pyartnet.output_correction.linear)
    return channel


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.get(DOMAIN, {})
    for fixture in data.get(DATA_FIXTURES, {}).values():
        fixture.stop()
    store = data.get(DATA_STORE)
    if store is not None:
        fixtures = data.get(DATA_FIXTURES, {})
        await store.async_save({k: f.state_dict() for k, f in fixtures.items()})
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
