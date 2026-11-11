"""HA-side persistent code library, learning, sending and receive events."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import logging
import time
from zoneinfo import ZoneInfo

from homeassistant.components import persistent_notification
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .binary import BinaryClient, IRCode, ProtocolError, received_code
from .const import DOMAIN, STORAGE_VERSION
from .groups import remote_groups

_LOGGER = logging.getLogger(__name__)


def names(value: Iterable[str], *, unique: bool = False) -> list[str]:
    """Validate names before any learning or transmission takes place."""
    result = [value] if isinstance(value, str) else list(value)
    if not result or len(result) > 50:
        raise ValueError("Provide between 1 and 50 command names")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in result):
        raise ValueError("Names must be nonempty strings of at most 100 characters")
    if unique and len(set(result)) != len(result):
        raise ValueError("Command names must be unique within one action")
    return result


def device_name(value: str | None) -> str:
    if value is None:
        return "default"
    names([value])
    return value


class Controller:
    def __init__(self, hass, entry, ip: str):
        self.hass, self.entry = hass, entry
        self.client = BinaryClient(ip, int(entry.data.get("port", 21000)))
        self.store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_codes")
        self.signal = f"{DOMAIN}_{entry.entry_id}_updated"
        self.codes: dict[str, dict[str, dict]] = {}
        self.version: dict[str, str] = {}
        self.online = False
        self.enabled = True
        self.learning = False
        self.last_error: str | None = None
        self.last_received: dict = {}
        self.last_sent: dict = {}
        self.received_count = 0
        self.sent_count = 0
        self.sensor_entity_id = None
        self.sent_sensor_entity_id = None
        self.receive_log_entity_id = None
        self.receive_log = deque(maxlen=100)
        self.receive_log_count = 0
        self._receive_chain = None
        self.groups = remote_groups(entry.options)
        self.slot_indices = [group["base"] + number for group in self.groups
                             for number in range(1, group["button_count"] + 1)]
        self.slot_count = len(self.slot_indices)
        self.slots = {str(index): self.default_slot(index)
                      for index in self.slot_indices}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._unsubscribe = None

    def group_for_slot(self, index):
        base = ((index - 1) // 1000) * 1000
        return next(group for group in self.groups if group["base"] == base)

    def slot_number(self, index):
        return (index - 1) % 1000 + 1

    def default_slot(self, index):
        group = self.group_for_slot(index)
        number = self.slot_number(index)
        device = "buttons" if group["id"] == "main" else f"remote_{group['id']}"
        return {"label": f"Button {number:02d}", "device": device,
                "command": f"button_{number:02d}"}

    def slot_learned(self, index):
        slot = self.slots[str(index)]
        return slot["command"] in self.codes.get(slot["device"], {})

    def command_info(self, device, command):
        for key, slot in self.slots.items():
            if slot["device"] == device and slot["command"] == command:
                return {"label": slot["label"], "slot": int(key)}
        return {"label": f"{device}/{command}", "slot": None}

    def bind_codes(self, codes, slots):
        """Offer codes learned by remote actions in unused, unnamed positions."""
        bound = {(slot["device"], slot["command"]) for slot in slots.values()}
        for device, commands in codes.items():
            for command in commands:
                if (device, command) in bound:
                    continue
                for index in self.slot_indices:
                    slot = slots[str(index)]
                    if (slot == self.default_slot(index)
                            and slot["command"] not in codes.get(slot["device"], {})):
                        slots[str(index)] = {"label": f"{device}/{command}"[:100],
                                             "device": device, "command": command}
                        bound.add((device, command))
                        break

    async def save_library(self, codes, slots=None):
        slots = deepcopy(self.slots) if slots is None else slots
        self.bind_codes(codes, slots)
        await self.store.async_save({"codes": codes, "slots": slots})
        self.codes, self.slots = codes, slots

    async def rename_slot(self, index, label):
        label = label.strip()
        if not label or len(label) > 100:
            raise ValueError("Button name must contain 1–100 characters")
        async with self.operation():
            slots = deepcopy(self.slots)
            slots[str(index)]["label"] = label
            await self.save_library(deepcopy(self.codes), slots)

    @property
    def library(self) -> dict[str, list[str]]:
        return {device: sorted(commands) for device, commands in self.codes.items()}

    @property
    def notification_id(self):
        return f"irtrans_learning_{self.entry.entry_id}"

    def notify(self):
        async_dispatcher_send(self.hass, self.signal)

    async def load(self):
        stored = await self.store.async_load()
        if stored is None:
            return
        if not isinstance(stored, dict) or not isinstance(stored.get("codes"), dict):
            raise ProtocolError("Invalid saved IRTrans library; restore its HA backup")
        # A corrupt file must never silently become an empty database and be overwritten.
        for device, commands in stored["codes"].items():
            device_name(device)
            if not isinstance(commands, dict):
                raise ProtocolError("Invalid saved IRTrans commands")
            for command, record in commands.items():
                names([command])
                if not isinstance(record, dict) or not isinstance(record.get("hex"), str):
                    raise ProtocolError("Invalid saved IRTrans code record")
                IRCode.from_hex(record["hex"]).signature()
                if "repeat_hex" in record:
                    IRCode.from_hex(record["repeat_hex"]).signature()
        self.codes = stored["codes"]
        if "slots" in stored:
            if not isinstance(stored["slots"], dict):
                raise ProtocolError("Invalid saved button positions")
            for key, slot in stored["slots"].items():
                if (not key.isdecimal() or not 1 <= int(key) <= 1000032
                        or not isinstance(slot, dict)
                        or not isinstance(slot.get("label"), str)
                        or not 1 <= len(slot["label"].strip()) <= 100):
                    raise ProtocolError("Invalid saved button position")
                device_name(slot.get("device"))
                names([slot.get("command")])
                self.slots[key] = {name:slot[name] for name in ("label","device","command")}
        self.bind_codes(self.codes, self.slots)

    async def probe(self):
        if self._closing or self._lock.locked():
            return
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            self.version = await self.client.version()
            self.online, self.last_error = True, None
        except (OSError, TimeoutError, ProtocolError) as exc:
            self.online, self.last_error = False, str(exc) or "IRTrans did not respond"
            raise
        finally:
            self._tasks.discard(task)
            self.notify()

    @asynccontextmanager
    async def operation(self):
        task = asyncio.current_task()
        if self._closing:
            raise ProtocolError("IRTrans integration is unloading")
        self._tasks.add(task)
        try:
            async with self._lock:
                if self._closing:
                    raise ProtocolError("IRTrans integration is unloading")
                if not self.enabled:
                    raise ProtocolError("Turn on the IRTrans remote entity first")
                yield
                self.last_error = None
        except (OSError, TimeoutError, ProtocolError, ValueError) as exc:
            self.last_error = str(exc) or "IRTrans request timed out"
            if isinstance(exc, (OSError, TimeoutError)):
                self.online = False
            raise
        finally:
            self._tasks.discard(task)
            self.notify()

    async def learn(self, device: str | None, commands: Iterable[str], *,
                    timeout: int = 30, raw: bool = False):
        device, commands = device_name(device), names(commands, unique=True)
        if not 5 <= timeout <= 120:
            raise ValueError("Learning timeout must be between 5 and 120 seconds")
        async with self.operation():
            self.learning = True
            self.notify()
            # Multi-command learning is committed as one transaction only after
            # all captures succeed. Existing records survive timeout/cancellation.
            updated = deepcopy(self.codes)
            try:
                for command in commands:
                    persistent_notification.async_create(
                        self.hass,
                        f"Point the original remote at IRTrans and briefly press **{self.command_info(device, command)['label']}** "
                        f"for **{device}**. Waiting up to {timeout} seconds.",
                        title="IRTrans: learn command", notification_id=self.notification_id,
                    )
                    code = await self.client.learn(raw=raw, timeout=timeout)
                    updated.setdefault(device, {})[command] = {
                        "hex": code.packet.hex(),
                        "learned_at": datetime.now(timezone.utc).isoformat(),
                        "raw": raw,
                    }
                await self.save_library(updated)
                self.online = True
                self.hass.bus.async_fire("irtrans_learned", {
                    "entry_id": self.entry.entry_id, "device": device, "commands": commands,
                })
            finally:
                self.learning = False
                self._receive_chain = None
                persistent_notification.async_dismiss(self.hass, self.notification_id)

    async def learn_repeat(self, device, command, *, timeout=30):
        device = device_name(device)
        names([command])
        if not 5 <= timeout <= 120:
            raise ValueError("Learning timeout must be between 5 and 120 seconds")
        async with self.operation():
            if command not in self.codes.get(device, {}):
                raise ValueError("Learn the main command first")
            self.learning = True
            self.notify()
            try:
                persistent_notification.async_create(
                    self.hass,
                    f"Hold **{self.command_info(device, command)['label']}** on the original "
                    "remote for 2–3 seconds to learn its repeat code.",
                    title="IRTrans: learn repeat", notification_id=self.notification_id,
                )
                record = self.codes[device][command]
                code = await self.client.learn(raw=record.get("raw", False), timeout=timeout, repeat=True)
                updated = deepcopy(self.codes)
                updated[device][command].update(
                    repeat_hex=code.packet.hex(),
                    repeat_learned_at=datetime.now(timezone.utc).isoformat(),
                )
                await self.save_library(updated)
                self.online = True
            finally:
                self.learning = False
                self._receive_chain = None
                persistent_notification.async_dismiss(self.hass, self.notification_id)

    def slot_repeat_learned(self, index):
        slot = self.slots[str(index)]
        return bool(self.codes.get(slot["device"], {}).get(slot["command"], {}).get("repeat_hex"))

    async def send(self, device: str | None, commands: Iterable[str], *,
                   repeats: int = 1, delay: float = .4, led: str = "default",
                   context=None, source: str = "HA action", hold_secs: float = 0):
        device, commands = device_name(device), names(commands)
        if not 1 <= repeats <= 100 or not 0 <= delay <= 60:
            raise ValueError("Repeats must be 1–100 and delay 0–60 seconds")
        if hold_secs and (not .1 <= hold_secs <= 10 or len(commands) != 1 or repeats != 1):
            raise ValueError("Hold requires one command, num_repeats=1 and 0.1–10 seconds")
        # Sending lists may legitimately contain repeated command names.
        async with self.operation():
            try:
                frames = [(name, IRCode.from_hex(self.codes[device][name]["hex"]))
                          for name in commands]
            except KeyError as exc:
                raise ValueError(f"Unknown command {device}/{exc.args[0]}; learn it first") from exc
            repeat_code = None
            if hold_secs:
                repeat_hex = self.codes[device][commands[0]].get("repeat_hex")
                if not repeat_hex:
                    raise ValueError("Use Learn repeat before sending a held command")
                repeat_code = IRCode.from_hex(repeat_hex)
            for repetition in range(repeats):
                for index, (command, code) in enumerate(frames):
                    if repetition or index:
                        await asyncio.sleep(delay)
                    frame_count = 1
                    if hold_secs:
                        frame_count = await self.client.send_hold(
                            code, repeat_code, seconds=hold_secs,
                            interval=float(self.entry.options.get("repeat_interval", .11)), led=led,
                        )
                    else:
                        await self.client.send(code, led)
                    self.online = True
                    self.sent_count += 1
                    self.last_sent = {"device": device, "command": command,
                                      **self.command_info(device, command), "source": source,
                                      "hold_secs": hold_secs, "frame_count": frame_count,
                                      "at": datetime.now(timezone.utc).isoformat()}
                    self.hass.bus.async_fire("irtrans_sent", {
                        "entry_id": self.entry.entry_id,
                        "entity_id": self.sent_sensor_entity_id, **self.last_sent,
                    }, context=context)
                    self.notify()

    async def delete(self, device: str | None, commands: Iterable[str]):
        device, commands = device_name(device), names(commands)
        async with self.operation():
            updated = deepcopy(self.codes)
            for command in commands:
                if device not in updated or command not in updated[device]:
                    raise ValueError(f"Unknown command {device}/{command}")
                del updated[device][command]
            if not updated[device]:
                del updated[device]
            await self.save_library(updated)

    def record_receive(self, data: bytes, *, code=None, matches=(), error=None, is_repeat=False, repeat_count=0):
        """Keep a bounded live log and describe every received device datagram."""
        now = datetime.now(timezone.utc)
        zone = getattr(getattr(self.hass, "config", None), "time_zone", "UTC")
        try:
            local_time = now.astimezone(ZoneInfo(zone)).strftime("%H:%M:%S")
        except (ValueError, KeyError):
            local_time = now.strftime("%H:%M:%S")
        self.receive_log_count += 1
        kind = "known" if matches else ("unknown" if code is not None else "packet")
        label = self.command_info(*matches[0])["label"] if matches else "unknown command"
        if error is not None:
            kind, label = "invalid", "invalid packet"
        elif code is None:
            label = f"packet type {data[0]}" if data else "empty packet"
        payload = code.data if code is not None else data
        readable = (payload.decode("ascii") if payload and all(32 <= byte < 127 for byte in payload)
                    else payload.hex())
        summary = label if matches else f"{label} · {readable[:64]}"
        if is_repeat:
            summary += f" · repeat {repeat_count}"
        record = {"number": self.receive_log_count, "at": now.isoformat(),
                  "time": local_time, "source": self.client.ip, "kind": kind,
                  "label": label, "packet_type": data[0] if data else None,
                  "raw_hex": data.hex(), "data": readable,
                  "fingerprint": code.fingerprint if code is not None else None,
                  "during_learning": self.learning, "is_repeat": is_repeat, "repeat_count": repeat_count,
                  "message": f"{local_time} · {summary}"}
        if error is not None:
            record["error"] = str(error)
        self.receive_log.appendleft(record)
        # Compact persistent Activity event; full packet bytes remain in the
        # bounded in-memory log and the last_packet sensor attribute.
        self.hass.bus.async_fire("irtrans_receive_log", {
            "entry_id": self.entry.entry_id, "entity_id": self.receive_log_entity_id,
            "message": f"received {summary}", "source": self.client.ip,
            "kind": kind, "fingerprint": record["fingerprint"],
            "packet_type": record["packet_type"], "number": record["number"],
            "during_learning": self.learning, "is_repeat": is_repeat, "repeat_count": repeat_count,
        })

    def receive(self, data: bytes):
        if self._closing or not self.enabled:
            return
        try:
            code = received_code(data)
            if code is None:
                self.record_receive(data)
                self.notify()
                return
            fingerprint = code.fingerprint
            matches = [(device, command) for device, commands in self.codes.items()
                       for command, record in commands.items()
                       if IRCode.from_hex(record["hex"]).matches(code)]
            now = time.monotonic()
            chain = self._receive_chain
            recent = chain is not None and now - chain[0] <= .5
            is_repeat, repeat_count = False, 0
            if not matches and recent:
                # A short repeat may be shared by many buttons: only associate
                # it with the immediately preceding, still-active named command.
                matches = [(device, command) for device, command in chain[1]
                           if "repeat_hex" in self.codes.get(device, {}).get(command, {})
                           and IRCode.from_hex(self.codes[device][command]["repeat_hex"]).matches(code)]
            if matches and recent and set(matches) == set(chain[1]):
                is_repeat, repeat_count = True, chain[2] + 1
            # Unknown traffic breaks the chain; learning cannot arm a named hold.
            self._receive_chain = (now, matches, repeat_count) if matches and not self.learning else None
        except ProtocolError as exc:
            self._receive_chain = None
            self.record_receive(data, error=exc)
            self.notify()
            return
        self.record_receive(data, code=code, matches=matches, is_repeat=is_repeat, repeat_count=repeat_count)
        self.online = True
        self.received_count += 1
        event = {"entry_id": self.entry.entry_id, "host": self.client.ip,
                 "fingerprint": fingerprint, "matched": bool(matches),
                 "is_repeat": is_repeat, "repeat_count": repeat_count,
                 "at": datetime.now(timezone.utc).isoformat()}
        self.last_received = dict(event)
        if matches:
            self.last_received.update({"device": matches[0][0], "command": matches[0][1],
                                       **self.command_info(*matches[0])})
        if self.learning:
            # Monitor learning traffic, but do not trigger command automations.
            self.notify()
            return
        self.hass.bus.async_fire("irtrans_received", event)
        for device, command in matches:
            self.hass.bus.async_fire("irtrans_event", {
                **event, "entity_id": self.sensor_entity_id,
                "type": "remote_pressed", "remote": device, "button": command,
                "device": device, "command": command, **self.command_info(device, command),
            })
        self.notify()

    async def close(self):
        self._closing = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        persistent_notification.async_dismiss(self.hass, self.notification_id)
