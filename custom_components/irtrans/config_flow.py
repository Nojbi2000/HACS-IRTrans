"""Configure direct IRTrans access, with no database-discovery prerequisite."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .binary import BinaryClient, ProtocolError, resolve_ipv4
from .const import DOMAIN


class IRTransFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input["host"].strip()
            port = int(user_input["port"])
            try:
                ip = await resolve_ipv4(host, port)
                await self.async_set_unique_id(ip)
                self._abort_if_unique_id_configured()
                # Earlier config entries did not have a unique ID.
                if any(entry.data.get("host") in (host, ip)
                       for entry in self._async_current_entries()):
                    return self.async_abort(reason="already_configured")
                await BinaryClient(ip, port).version()
            except (OSError, TimeoutError, ProtocolError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="IRTrans", data={**user_input, "host": host})
        defaults = user_input or {}
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("host", default=defaults.get("host", "")): str,
            vol.Required("port", default=defaults.get("port", 21000)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required("receive_port", default=defaults.get("receive_port", 21000)):
                vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IRTransOptionsFlow()


class IRTransOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("led", default=self.config_entry.options.get("led", "default")):
                vol.In(["default", "internal", "external", "both"]),
        }))
