"""Native Learn/Send buttons on the IRTrans device page."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from .binary import ProtocolError
from .const import DOMAIN
from .entity import IRTransEntity


async def async_setup_entry(hass, entry, async_add_entities):
    controller = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities([IRTransCommandButton(controller, index, action)
                        for index in controller.slot_indices
                        for action in ("learn", "send")])


class IRTransCommandButton(IRTransEntity, ButtonEntity):
    def __init__(self, controller, index, action):
        super().__init__(controller)
        self.index, self.action = index, action
        self.remote_group = controller.group_for_slot(index)
        self.number = controller.slot_number(index)
        self._attr_unique_id = f"{controller.entry.entry_id}-slot-{index}-{action}"
        self._attr_icon = "mdi:remote" if action == "send" else "mdi:record-rec"

    @property
    def name(self):
        label = self.controller.slots[str(self.index)]["label"]
        suffix = " (not learned)" if self.action == "send" and not self.controller.slot_learned(self.index) else ""
        return f"{self.number:02d} {self.action.capitalize()} · {label}{suffix}"

    @property
    def available(self):
        return (self.controller.online and self.controller.enabled and not self.controller.learning
                and (self.action == "learn" or self.controller.slot_learned(self.index)))

    @property
    def extra_state_attributes(self):
        return {"slot": self.index, "action": self.action,
                **self.controller.slots[str(self.index)],
                "learned": self.controller.slot_learned(self.index)}

    async def async_press(self):
        slot = dict(self.controller.slots[str(self.index)])
        try:
            if self.action == "learn":
                await self.controller.learn(slot["device"], [slot["command"]])
            else:
                await self.controller.send(
                    slot["device"], [slot["command"]], context=self._context,
                    source=f"{self.remote_group['name']} / Button {self.number:02d}",
                    led=self.controller.entry.options.get("led", "default"),
                )
        except (ValueError, OSError, TimeoutError, ProtocolError) as exc:
            raise HomeAssistantError(str(exc) or "IRTrans did not respond") from exc
