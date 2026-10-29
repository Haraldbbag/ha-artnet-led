"""Shared creation of Art-Net / sACN / KiNET nodes.

Lights from the ``light:`` platform and fixtures from the ``artnet_led:`` block
must talk through the same node object, otherwise two senders would fight over
one universe. All node creation therefore goes through ``async_get_node``.
"""
from __future__ import annotations

import asyncio

import pyartnet
from homeassistant.core import HomeAssistant

ARTNET_DEFAULT_PORT = 6454
SACN_DEFAULT_PORT = 5568
KINET_DEFAULT_PORT = 6038

NODE_TYPES = ["artnet-direct", "artnet-controller", "sacn", "kinet"]

NODES: dict[str, pyartnet.base.BaseNode] = {}
_LOCK = asyncio.Lock()


async def async_get_node(
    hass: HomeAssistant,
    client_type: str,
    host: str,
    port: int | None,
    host_override: str | None,
    port_override: int | None,
    max_fps: int,
    refresh_interval: float,
) -> pyartnet.base.BaseNode:
    """Return the node for host/port, creating it on first use."""
    # pyartnet expects the created asyncio task to be the task running its wrapper.
    # Home Assistant's task factory can wrap tasks, which breaks pyartnet's assertions.
    pyartnet.base.background_task.CREATE_TASK = asyncio.create_task

    real_host = host_override or host
    real_port = port_override if port_override is not None else port

    async with _LOCK:
        if client_type == "artnet-controller":
            if "server" not in NODES:
                from custom_components.artnet_led.bridge.artnet_controller import ArtNetController

                node = ArtNetController(hass, max_fps=max_fps, refresh_every=refresh_interval)
                NODES["server"] = node
                node.start()
            return NODES["server"]

        node_id = f"{host}:{port}"
        if node_id in NODES:
            return NODES[node_id]

        from pyartnet.base.network import UnicastNetworkTarget

        if client_type == "artnet-direct":
            target = UnicastNetworkTarget.create(real_host, real_port or ARTNET_DEFAULT_PORT)
            node = pyartnet.ArtNetNode(target, max_fps=max_fps, refresh_every=refresh_interval)
        elif client_type == "sacn":
            target = UnicastNetworkTarget.create(real_host, real_port or SACN_DEFAULT_PORT)
            node = pyartnet.SacnNode(target, max_fps=max_fps, refresh_every=refresh_interval,
                                     source_name="ha-artnet-led")
        elif client_type == "kinet":
            target = UnicastNetworkTarget.create(real_host, real_port or KINET_DEFAULT_PORT)
            node = pyartnet.KiNetNode(target, max_fps=max_fps, refresh_every=refresh_interval)
        else:
            raise NotImplementedError(f"Unknown client type '{client_type}'")

        await node.__aenter__()
        if not refresh_interval:
            await node.stop_refresh()
        NODES[node_id] = node
        return node
