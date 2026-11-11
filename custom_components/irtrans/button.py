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
                        for action in ("learn", "learn_repeat", "send", "hold")])


class IRTransCommandButton(IRTransEntity, ButtonEntity):
    def __init__(self, controller, index, action):
        super().__init__(controller)
        self.index, self.action = index, action
        self.remote_group = controller.group_for_slot(index)
        self.number = controller.slot_number(index)
        self._attr_unique_id = f"{controller.entry.entry_id}-slot-{index}-{action}"
        self._attr_icon = "mdi:remote" if action in ("send", "hold") else "mdi:record-rec"

    @property
    def name(self):
        label = self.controller.slots[str(self.index)]["label"]
        suffix = " (not learned)" if self.action in ("send", "hold") and not self.controller.slot_learned(self.index) else ""
        if self.action == "hold" and not suffix and not self.controller.slot_repeat_learned(self.index):
            suffix = " (repeat not learned)"
        action = {"learn": "Learn", "learn_repeat": "Learn repeat", "send": "Send",
                  "hold": f"Hold {self.controller.entry.options.get('hold_secs', 1):g} s"}[self.action]
        return f"{self.number:02d} {action} · {label}{suffix}"

    @property
    def available(self):
        return (self.controller.online and self.controller.enabled and not self.controller.learning
                and (self.action == "learn" or self.controller.slot_learned(self.index))
                and (self.action != "hold" or self.controller.slot_repeat_learned(self.index)))

    @property
    def extra_state_attributes(self):
        return {"slot": self.index, "action": self.action,
                **self.controller.slots[str(self.index)],
                "learned": self.controller.slot_learned(self.index),
                "repeat_learned": self.controller.slot_repeat_learned(self.index),
                "short_samples": self.controller.codes.get(self.controller.slots[str(self.index)]["device"], {}).get(
                    self.controller.slots[str(self.index)]["command"], {}).get("sample_count", 0),
                "repeat_samples": self.controller.codes.get(self.controller.slots[str(self.index)]["device"], {}).get(
                    self.controller.slots[str(self.index)]["command"], {}).get("repeat_sample_count", 0)}

    async def async_press(self):
        slot = dict(self.controller.slots[str(self.index)])
        try:
            if self.action == "learn":
                await self.controller.learn(slot["device"], [slot["command"]])
            elif self.action == "learn_repeat":
                await self.controller.learn_repeat(slot["device"], slot["command"])
            else:
                await self.controller.send(
                    slot["device"], [slot["command"]], context=self._context,
                    source=f"{self.remote_group['name']} / Button {self.number:02d}",
                    led=self.controller.entry.options.get("led", "default"),
                    hold_secs=float(self.controller.entry.options.get("hold_secs", 1)) if self.action == "hold" else 0,
                )
        except (ValueError, OSError, TimeoutError, ProtocolError) as exc:
            raise HomeAssistantError(str(exc) or "IRTrans did not respond") from exc
