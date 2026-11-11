"""Live receive buffer and learned signal summaries for troubleshooting."""
from .binary import IRCode
from .const import DOMAIN


def signal_summary(value):
    code = IRCode.from_hex(value)
    payload = code.pulse_distance_payload()
    return {"mode": code.mode, "fingerprint": code.fingerprint,
            "symbol_data": code.signature().decode("ascii", errors="backslashreplace"),
            "payload_32": payload.hex() if payload is not None else None,
            "short_repeat": code.is_short_repeat(),
            "timing_pairs_us": code.pulse_distance_timings(),
            "carrier": code.packet[7], "packet_length": len(code.packet)}


async def async_get_config_entry_diagnostics(hass, entry):
    controller = hass.data[DOMAIN]["entries"][entry.entry_id]
    learned = []
    for device, commands in controller.codes.items():
        for command, record in commands.items():
            learned.append({"device": device, "command": command,
                            "label": controller.command_info(device, command)["label"],
                            "short_samples": record.get("sample_count", 1),
                            "repeat_samples": record.get("repeat_sample_count", int("repeat_hex" in record)),
                            "main": signal_summary(record["hex"]),
                            "repeat": signal_summary(record["repeat_hex"]) if "repeat_hex" in record else None,
                            "repeat_variants": [signal_summary(value) for value in record.get("repeat_variants", [])]})
    return {"version": dict(controller.version),
            "received_packets": controller.receive_log_count,
            "receive_log": list(controller.receive_log),
            "learned_signals": learned,
            "last_error": controller.last_error}
