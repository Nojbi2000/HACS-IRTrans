"""Standard Home Assistant learning, sending and deletion actions."""
from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.exceptions import HomeAssistantError
from .binary import ProtocolError
from .const import DOMAIN
from .entity import IRTransEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([IRTransRemote(hass.data[DOMAIN]["entries"][entry.entry_id])])


class IRTransRemote(IRTransEntity, RemoteEntity):
    _attr_name = "Remote"
    _attr_supported_features = RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND

    def __init__(self, controller):
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry.entry_id}-remote"

    @property
    def is_on(self):
        return self.controller.enabled

    @property
    def extra_state_attributes(self):
        return {"commands": self.controller.library, "learning": self.controller.learning,
                "last_error": self.controller.last_error,
                "last_sent": self.controller.last_sent}

    async def async_turn_on(self, **kwargs):
        self.controller.enabled = True
        self.controller.notify()

    async def async_turn_off(self, **kwargs):
        if self.controller.learning:
            raise HomeAssistantError("Wait for learning to finish before disabling IRTrans")
        self.controller.enabled = False
        self.controller.notify()

    async def async_learn_command(self, **kwargs):
        try:
            kind = kwargs.get("command_type", "ir")
            if kind not in ("ir", "raw"):
                raise ValueError("IRTrans supports command_type ir or raw")
            if kwargs.get("alternative", False):
                raise ValueError("Alternative-code learning is not supported; use a separate command name")
            await self.controller.learn(kwargs.get("device"), kwargs.get("command") or [],
                                        timeout=kwargs.get("timeout", 30), raw=kind == "raw")
        except (ValueError, ProtocolError, OSError, TimeoutError) as exc:
            raise HomeAssistantError(str(exc) or "IRTrans learning timed out; existing codes were preserved") from exc

    async def async_send_command(self, command, **kwargs):
        try:
            await self.controller.send(
                kwargs.get("device"), command, repeats=kwargs.get("num_repeats", 1),
                delay=kwargs.get("delay_secs", .4),
                led=self.controller.entry.options.get("led", "default"),
                context=self._context, hold_secs=kwargs.get("hold_secs", 0),
            )
        except (ValueError, ProtocolError, OSError, TimeoutError) as exc:
            raise HomeAssistantError(str(exc) or "IRTrans transmission was not acknowledged") from exc

    async def async_delete_command(self, **kwargs):
        try:
            await self.controller.delete(kwargs.get("device"), kwargs.get("command") or [])
        except (ValueError, ProtocolError, OSError) as exc:
            raise HomeAssistantError(str(exc)) from exc
