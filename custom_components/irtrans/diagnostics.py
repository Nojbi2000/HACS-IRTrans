"""Download the live receive buffer without exporting the stored IR library."""
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    controller = hass.data[DOMAIN]["entries"][entry.entry_id]
    return {"version": dict(controller.version),
            "received_packets": controller.receive_log_count,
            "receive_log": list(controller.receive_log),
            "last_error": controller.last_error}
