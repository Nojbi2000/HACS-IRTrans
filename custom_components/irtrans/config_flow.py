"""Configure direct IRTrans access, with no database-discovery prerequisite."""
from __future__ import annotations

import voluptuous as vol
from uuid import uuid4
from .groups import remote_groups
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
        return self.async_show_menu(step_id="init", menu_options=["add_remote", "select_remote", "output"])

    def save_groups(self, groups):
        return self.async_create_entry(title="", data={**self.config_entry.options, "remotes": groups})

    async def async_step_add_remote(self, user_input=None):
        groups = remote_groups(self.config_entry.options)
        errors = {}
        if user_input is not None:
            name = user_input["name"].strip()
            if not name or len(name) > 100:
                errors["name"] = "invalid_name"
            else:
                groups.append({"id": uuid4().hex, "name": name,
                               "base": max(group["base"] for group in groups) + 1000,
                               "button_count": user_input["button_count"]})
                return self.save_groups(groups)
        return self.remote_form("add_remote", user_input or {
            "name": f"Remote {len(groups) + 1:02d}", "button_count": 8}, errors)

    async def async_step_select_remote(self, user_input=None):
        groups = remote_groups(self.config_entry.options)
        if user_input is not None:
            self.selected_remote = user_input["remote"]
            return await self.async_step_edit_remote()
        return self.async_show_form(step_id="select_remote", data_schema=vol.Schema({
            vol.Required("remote"): vol.In({group["id"]: group["name"] for group in groups}),
        }))

    async def async_step_edit_remote(self, user_input=None):
        groups = remote_groups(self.config_entry.options)
        group = next(group for group in groups if group["id"] == self.selected_remote)
        errors = {}
        if user_input is not None:
            name = user_input["name"].strip()
            if not name or len(name) > 100:
                errors["name"] = "invalid_name"
            else:
                group.update(name=name, button_count=user_input["button_count"])
                return self.save_groups(groups)
        return self.remote_form("edit_remote", user_input or group, errors)

    def remote_form(self, step, defaults, errors):
        return self.async_show_form(step_id=step, data_schema=vol.Schema({
            vol.Required("name", default=defaults["name"]): str,
            vol.Required("button_count", default=defaults["button_count"]):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
        }), errors=errors)

    async def async_step_output(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})
        return self.async_show_form(step_id="output", data_schema=vol.Schema({
            vol.Required("led", default=self.config_entry.options.get("led", "default")):
                vol.In(["default", "internal", "external", "both"]),
            vol.Required("hold_secs", default=self.config_entry.options.get("hold_secs", 1)):
                vol.All(vol.Coerce(float), vol.Range(min=.1, max=10)),
            vol.Required("repeat_interval", default=self.config_entry.options.get("repeat_interval", .11)):
                vol.All(vol.Coerce(float), vol.Range(min=.03, max=1)),
        }))
