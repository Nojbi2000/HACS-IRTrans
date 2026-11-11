"""Shared device information and push updates."""
from homeassistant.helpers import device_registry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN


class IRTransEntity:
    """Mixin shared by the sensor and remote."""
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller):
        self.controller = controller

    @property
    def available(self):
        return self.controller.online

    @property
    def device_info(self):
        if hasattr(self, "remote_group"):
            return {
                "identifiers": {(DOMAIN, f"{self.controller.entry.entry_id}-remote-{self.remote_group['id']}")},
                "name": self.remote_group["name"], "manufacturer": "IRTrans GmbH",
                "model": "IR remote (codes stored in HA)",
                "via_device_id": device_registry.async_get(self.controller.hass).async_get_device(
                    identifiers={(DOMAIN, self.controller.entry.entry_id)}).id,
            }
        version = self.controller.version
        return {
            "identifiers": {(DOMAIN, self.controller.entry.entry_id)},
            "name": "IRTrans", "manufacturer": "IRTrans GmbH",
            "model": "IRTrans LAN (direct)",
            "sw_version": f"{version.get('lan', 'unknown')} / {version.get('ir', 'unknown')}",
            "configuration_url": f"http://{self.controller.client.ip}",
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.controller.signal, self.async_write_ha_state
        ))
