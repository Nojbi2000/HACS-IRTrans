"""Last received command and local command-library summary."""
from homeassistant.components.sensor import SensorEntity
from .const import DOMAIN
from .entity import IRTransEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([IRTransSensor(hass.data[DOMAIN]["entries"][entry.entry_id])])


class IRTransSensor(IRTransEntity, SensorEntity):
    _attr_name = "Last received command"
    _attr_icon = "mdi:remote"

    def __init__(self, controller):
        super().__init__(controller)
        self._attr_unique_id = controller.entry.entry_id  # preserve original sensor registry ID

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.controller.sensor_entity_id = self.entity_id

    @property
    def native_value(self):
        received = self.controller.last_received
        if received.get("matched"):
            return f"{received['device']}/{received['command']}"
        return "unknown command" if received else "listening"

    @property
    def extra_state_attributes(self):
        return {"commands": self.controller.library,
                "learning": self.controller.learning,
                "received_count": self.controller.received_count,
                "last_received": self.controller.last_received,
                "last_error": self.controller.last_error,
                "receive_port": self.controller.entry.data.get("receive_port", 21000)}
