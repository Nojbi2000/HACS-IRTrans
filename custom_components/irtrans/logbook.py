"""Describe actual IR activity, including the standard HA actor context."""
from homeassistant.core import callback
from .const import DOMAIN


@callback
def async_describe_events(hass, async_describe_event):
    @callback
    def describe_received(event):
        data = event.data
        return {"name": "IRTrans", "message": data.get("message", "received IR data"),
                "entity_id": data.get("entity_id")}

    @callback
    def describe_sent(event):
        data = event.data
        return {"name": "IRTrans", "message": f"sent {data.get('label', data.get('command', 'command'))}",
                "entity_id": data.get("entity_id")}

    async_describe_event(DOMAIN, "irtrans_receive_log", describe_received)
    async_describe_event(DOMAIN, "irtrans_sent", describe_sent)
