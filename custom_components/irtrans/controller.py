"""HA-side persistent code library, learning, sending and receive events."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import logging

from homeassistant.components import persistent_notification
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .binary import BinaryClient, IRCode, ProtocolError, received_code
from .const import DOMAIN, STORAGE_VERSION

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
        self.sensor_entity_id = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._unsubscribe = None

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
        self.codes = stored["codes"]

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
                        f"Point the original remote at IRTrans and briefly press **{command}** "
                        f"for **{device}**. Waiting up to {timeout} seconds.",
                        title="IRTrans: learn command", notification_id=self.notification_id,
                    )
                    code = await self.client.learn(raw=raw, timeout=timeout)
                    updated.setdefault(device, {})[command] = {
                        "hex": code.packet.hex(),
                        "learned_at": datetime.now(timezone.utc).isoformat(),
                        "raw": raw,
                    }
                await self.store.async_save({"codes": updated})
                self.codes = updated
                self.online = True
                self.hass.bus.async_fire("irtrans_learned", {
                    "entry_id": self.entry.entry_id, "device": device, "commands": commands,
                })
            finally:
                self.learning = False
                persistent_notification.async_dismiss(self.hass, self.notification_id)

    async def send(self, device: str | None, commands: Iterable[str], *,
                   repeats: int = 1, delay: float = .4, led: str = "default"):
        device, commands = device_name(device), names(commands)
        if not 1 <= repeats <= 100 or not 0 <= delay <= 60:
            raise ValueError("Repeats must be 1–100 and delay 0–60 seconds")
        # Sending lists may legitimately contain repeated command names.
        async with self.operation():
            try:
                frames = [(name, IRCode.from_hex(self.codes[device][name]["hex"]))
                          for name in commands]
            except KeyError as exc:
                raise ValueError(f"Unknown command {device}/{exc.args[0]}; learn it first") from exc
            for repetition in range(repeats):
                for index, (command, code) in enumerate(frames):
                    if repetition or index:
                        await asyncio.sleep(delay)
                    await self.client.send(code, led)
                    self.online = True
                    self.last_sent = {"device": device, "command": command,
                                      "at": datetime.now(timezone.utc).isoformat()}
            self.hass.bus.async_fire("irtrans_sent", {
                "entry_id": self.entry.entry_id, **self.last_sent,
            })

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
            await self.store.async_save({"codes": updated})
            self.codes = updated

    def receive(self, data: bytes):
        if self._closing or not self.enabled or self.learning:
            return
        try:
            code = received_code(data)
            if code is None:
                return
            fingerprint = code.fingerprint
            matches = [(device, command) for device, commands in self.codes.items()
                       for command, record in commands.items()
                       if IRCode.from_hex(record["hex"]).matches(code)]
        except ProtocolError as exc:
            _LOGGER.debug("Ignoring invalid IRTrans receive packet: %s", exc)
            return
        self.online = True
        self.received_count += 1
        event = {"entry_id": self.entry.entry_id, "host": self.client.ip,
                 "fingerprint": fingerprint, "matched": bool(matches),
                 "at": datetime.now(timezone.utc).isoformat()}
        self.last_received = dict(event)
        if matches:
            self.last_received.update({"device": matches[0][0], "command": matches[0][1]})
        self.hass.bus.async_fire("irtrans_received", event)
        # Duplicate aliases intentionally produce an event per matching stored name.
        for device, command in matches:
            self.hass.bus.async_fire("irtrans_event", {
                **event, "entity_id": self.sensor_entity_id,
                "type": "remote_pressed", "remote": device, "button": command,
                "device": device, "command": command,
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
