"""Direct IRTrans LAN integration with codes stored in Home Assistant."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .binary import ProtocolError, ReceiveHub, resolve_ipv4
from .const import DOMAIN, PLATFORMS
from .controller import Controller

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    state = hass.data.setdefault(DOMAIN, {"entries": {}, "hub": None})
    # Serialize startup of entries sharing one UDP listener.
    lock = state.setdefault("setup_lock", asyncio.Lock())
    async with lock:
        controller = None
        stage = "resolving the IRTrans address"
        try:
            ip = await resolve_ipv4(entry.data["host"], int(entry.data.get("port", 21000)))
            if any(item.client.ip == ip for item in state["entries"].values()):
                raise ProtocolError("This IRTrans device is already configured")
            controller = Controller(hass, entry, ip)
            stage = "loading the HA code library"
            await controller.load()
            stage = "querying IRTrans firmware via binary UDP"
            await controller.probe()
            if state["hub"] is None:
                hub = ReceiveHub()
                stage = "opening the HA UDP receive port"
                await hub.start(int(entry.data.get("receive_port", 21000)))
                state["hub"] = hub
            elif state["hub"].transport.get_extra_info("sockname")[1] != int(entry.data.get("receive_port", 21000)):
                raise ProtocolError("All IRTrans entries must use the same receive port")
            state["entries"][entry.entry_id] = controller
            state["hub"].handlers[ip] = controller.receive
            stage = "setting up HA entities"
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        except (Exception, asyncio.CancelledError) as exc:
            if controller is not None:
                await controller.close()
                state["entries"].pop(entry.entry_id, None)
                if state["hub"] is not None:
                    state["hub"].handlers.pop(controller.client.ip, None)
            if not state["entries"] and state["hub"] is not None:
                state["hub"].close()
                state["hub"] = None
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ConfigEntryNotReady(
                f"IRTrans failed while {stage}: {str(exc) or type(exc).__name__}"
            ) from exc

    async def heartbeat(_now):
        try:
            await controller.probe()
        except (OSError, TimeoutError, ProtocolError) as exc:
            _LOGGER.debug("IRTrans heartbeat: %s", exc)

    controller._unsubscribe = async_track_time_interval(hass, heartbeat, timedelta(seconds=60))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    state = hass.data[DOMAIN]
    async with state["setup_lock"]:
        controller = state["entries"].pop(entry.entry_id)
        state["hub"].handlers.pop(controller.client.ip, None)
        await controller.close()
        if not state["entries"]:
            state["hub"].close()
            state["hub"] = None
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)
