"""Last received command and local command-library summary."""
from homeassistant.components.sensor import SensorEntity
from .const import DOMAIN
from .entity import IRTransEntity


async def async_setup_entry(hass, entry, async_add_entities):
    controller = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities([IRTransSensor(controller), IRTransSentSensor(controller), IRTransReceiveLogSensor(controller),
                        *[IRTransLearningSensor(controller, group) for group in controller.groups]])


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
            return received.get("label", f"{received['device']}/{received['command']}")
        return "unknown command" if received else "listening"

    @property
    def extra_state_attributes(self):
        return {"commands": self.controller.library,
                "learning": self.controller.learning,
                "received_count": self.controller.received_count,
                "last_received": self.controller.last_received,
                "last_error": self.controller.last_error,
                "receive_port": self.controller.entry.data.get("receive_port", 21000)}


class IRTransSentSensor(IRTransEntity, SensorEntity):
    _attr_name = "Last sent command"
    _attr_icon = "mdi:remote-tv"

    def __init__(self, controller):
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry.entry_id}-last-sent"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.controller.sent_sensor_entity_id = self.entity_id

    @property
    def native_value(self):
        return self.controller.last_sent.get("label", "nothing sent yet")

    @property
    def extra_state_attributes(self):
        return {"sent_count": self.controller.sent_count, **self.controller.last_sent}


class IRTransReceiveLogSensor(IRTransEntity, SensorEntity):
    _attr_name = "Receive log"
    _attr_icon = "mdi:format-list-bulleted"
    _unrecorded_attributes = frozenset({"entries", "last_packet"})

    def __init__(self, controller):
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry.entry_id}-receive-log"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.controller.receive_log_entity_id = self.entity_id

    @property
    def native_value(self):
        if not self.controller.receive_log:
            return "waiting for received data"
        return self.controller.receive_log[0]["message"][:255]

    @property
    def extra_state_attributes(self):
        records = list(self.controller.receive_log)
        return {"received_packets": self.controller.receive_log_count,
                "entries": [record["message"] for record in records[:20]],
                "last_packet": dict(records[0]) if records else None,
                "buffer_size": 100}


class IRTransLearningSensor(IRTransEntity, SensorEntity):
    """Keep instructions visible even when controls are disabled or offline."""
    _attr_name = "Learning status"
    _attr_icon = "mdi:progress-question"

    def __init__(self, controller, group):
        super().__init__(controller)
        self.remote_group = group
        self._attr_unique_id = f"{controller.entry.entry_id}-remote-{group['id']}-learning-status"

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        controller = self.controller
        if not controller.learning and not controller.online:
            return "IRTrans unavailable · check power and connection"
        if not controller.enabled:
            return "Remote disabled · turn on the IRTrans Remote entity"
        if controller.learning and controller.learning_progress["phase"] == "idle":
            return "Preparing learning"
        return controller.learning_progress["message"][:255]

    @property
    def extra_state_attributes(self):
        return {**self.controller.learning_progress,
                "learning": self.controller.learning, "online": self.controller.online,
                "last_error": self.controller.last_error}
