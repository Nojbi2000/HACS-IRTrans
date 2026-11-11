"""Persistent names for command positions, editable without YAML."""
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.exceptions import HomeAssistantError
from .binary import ProtocolError
from .const import DOMAIN
from .entity import IRTransEntity


async def async_setup_entry(hass, entry, async_add_entities):
    controller = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities([IRTransButtonName(controller, index)
                        for index in range(1, controller.slot_count + 1)])


class IRTransButtonName(IRTransEntity, TextEntity):
    _attr_native_min = 1
    _attr_native_max = 100
    _attr_mode = TextMode.TEXT
    _attr_icon = "mdi:form-textbox"

    def __init__(self, controller, index):
        super().__init__(controller)
        self.index = index
        self._attr_unique_id = f"{controller.entry.entry_id}-slot-{index}-name"
        self._attr_name = f"{index:02d} Name"

    @property
    def available(self):
        return self.controller.online and not self.controller.learning

    @property
    def native_value(self):
        return self.controller.slots[str(self.index)]["label"]

    @property
    def extra_state_attributes(self):
        return {"slot": self.index, **self.controller.slots[str(self.index)],
                "learned": self.controller.slot_learned(self.index)}

    async def async_set_value(self, value):
        try:
            await self.controller.rename_slot(self.index, value)
        except (ValueError, OSError, ProtocolError) as exc:
            raise HomeAssistantError(str(exc)) from exc
