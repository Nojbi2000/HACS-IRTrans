"""Direct IRTrans LAN binary UDP transport (no irserver or IRDB required).

Wire layout and matching are derived from IRTrans 6.10.23 remote.h,
lanio.c, lowlevel.c, fileio.c and server.c. See THIRD-PARTY-NOTICES.txt.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import socket
import struct
from collections.abc import Callable

VERSION = 231
LEARN = 242
RECEIVED = {230, 246, 247}
ACK = 212
ACK_WAIT = 213
ACK_DUPLICATE = 214
ACK_BUSY = 215


class ProtocolError(Exception):
    """Invalid/unsupported response or a negative device acknowledgement."""


def checksum(packet: bytes) -> bytes:
    """IRDATA checksum is the byte sum starting at command (offset 2)."""
    result = bytearray(packet)
    result[1] = sum(result[2:]) & 255
    return bytes(result)


def control(command: int, tail: bytes) -> bytes:
    """Build a short IRDATA controller command."""
    return checksum(bytes((3 + len(tail), 0, command)) + tail)


def version_command() -> bytes:
    # HOST_VERSION, VERSION_MAGIC_5/2/3/4; packed little-endian IRDATA.
    return control(2, bytes((0x27, 0x0B, 0x64, 0xB3, 0x8B)))


def learn_command(raw: bool = False) -> bytes:
    # LearnIREx with configured receiver/timeout/tolerance, no persistent changes.
    return control(82 if raw else 18, b"\0\0\0\0")


def envelope(packet: bytes, result_port: int, sequence: int = 0) -> bytes:
    """IRDATA_LAN: command, mode, result_port, 16 uint32 send masks, IRDATA."""
    if not 0 < result_port <= 65535 or not 0 <= sequence <= 255:
        raise ValueError("Invalid UDP port/sequence")
    return struct.pack("<BBH16I", 200, sequence, result_port, *([0] * 16)) + packet


@dataclass(frozen=True)
class IRCode:
    """Full device-native IRDATA, including timings and carrier for replay."""
    packet: bytes
    mode: int
    data: bytes

    @classmethod
    def parse(cls, packet: bytes) -> IRCode:
        if not 9 <= len(packet) <= 255 or packet[0] != len(packet):
            raise ProtocolError("Invalid IRDATA packet length")
        mode, size = packet[8], packet[6]
        if not size:
            raise ProtocolError("IRDATA contains no IR code")
        if mode & 128:
            special = mode & 159
            if special == 128:
                offset, time_pos, max_timings = 84, 81, 18
            elif special == 131:
                offset, time_pos, max_timings = 54, 51, 20
            elif special in (129, 130, 132, 133, 134):
                offset, time_pos, max_timings = 44, 41, 8
            else:
                raise ProtocolError(f"Unsupported IRDATA special mode {mode}")
        elif mode & 16:
            offset, time_pos, max_timings = 9, None, 0
        elif mode & 32:
            raise ProtocolError("Non-IR/macro data is not a learnable IR code")
        else:
            offset, time_pos, max_timings = 44, 41, 8
        end = offset + size
        # Some RC6 frames include one extra byte. Preserve it for replay.
        if len(packet) not in (end, end + 1):
            raise ProtocolError("IRDATA code length does not match its payload")
        if len(packet) == end + 1 and packet[end] and not mode & (8 | 16):
            raise ProtocolError("Unexpected trailing IRDATA byte")
        if time_pos is not None and packet[time_pos] > max_timings:
            raise ProtocolError("IRDATA timing table is too large")
        return cls(bytes(packet), mode, packet[offset:end])

    @classmethod
    def from_hex(cls, value: str) -> IRCode:
        try:
            return cls.parse(bytes.fromhex(value))
        except (TypeError, ValueError) as exc:
            raise ProtocolError("Invalid stored IRDATA hex") from exc

    def for_send(self, led: str = "default") -> bytes:
        """Retain original timings, carrier and repeat metadata; replace routing."""
        outputs = {"default": 0, "internal": 1, "external": 2, "both": 3}
        if led not in outputs:
            raise ValueError("Unknown IR LED output")
        packet = bytearray(self.packet)
        packet[2] = 6  # HOST_SEND
        packet[3] = outputs[led]
        packet[4:6] = b"\xff\xff"  # all serial-bus targets, local LAN device selected by IP
        return checksum(packet)

    def signature(self) -> bytes:
        """Match decoded data rather than volatile carrier/timing measurements."""
        data = self.data
        if self.mode & 16 and not self.mode & 128:
            return data  # RAW is compared with tolerance separately.
        if data.startswith(b"Xl"):
            data = data[2:]
        if data and data[0] < 48:
            # Calibration/prefix length includes its leading length byte.
            if data[0] == 0 or data[0] >= len(data):
                raise ProtocolError("Invalid IRDATA calibration prefix")
            data = data[data[0]:]
        if data.startswith(b"Xl"):
            data = data[2:]
        if data and data[0] & 128:
            odd = bool(data[0] & 8)
            count = len(data) * 2 - int(odd)
            data = bytes(value for byte in data for value in
                         ((byte & 7) + 48, ((byte >> 4) & 7) + 48))[:count]
        result = bytearray(data)
        if not self.mode & 128:
            # Hardware detection normally filters toggles; tolerate both states
            # at the documented RC5 / RC6 toggle positions as well.
            if self.mode & 12 == 4 and len(result) > 2:
                result[2] = ord("0")
            elif self.mode & 12 == 8:
                index = 23 if len(result) == 40 else 5
                if len(result) > index:
                    result[index] = ord("0")
        if not result:
            raise ProtocolError("IRDATA has an empty receive signature")
        return bytes(result)

    @property
    def family(self) -> int:
        return self.mode & 159 if self.mode & 128 else self.mode & 28

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(bytes((self.family,)) + self.signature()).hexdigest()[:24]

    def matches(self, other: IRCode) -> bool:
        if self.family != other.family:
            return False
        if self.family == 16:
            own, received = _raw_values(self.data), _raw_values(other.data)
            return len(own) == len(received) and all(
                abs(a - b) <= 15 + (a >> 5) for a, b in zip(own, received)
            )
        return self.signature() == other.signature()


def _raw_values(data: bytes) -> list[int]:
    values, pos = [], 0
    while pos < len(data):
        value = data[pos]
        pos += 1
        if value == 0:
            if pos + 2 > len(data):
                raise ProtocolError("Truncated RAW duration")
            value = data[pos] * 256 + data[pos + 1]
            pos += 2
        values.append(value)
    return values


def parse_version(data: bytes) -> dict[str, str]:
    if len(data) < 32 or data[0] != VERSION or data[1] + 1 != len(data):
        raise ProtocolError("Invalid IRTrans version response")
    try:
        ir = data[2:10].rstrip(b"\0").decode("ascii")
        lan = data[18:26].rstrip(b"\0").decode("ascii")
    except UnicodeError as exc:
        raise ProtocolError("Invalid IRTrans firmware text") from exc
    return {"ir": ir, "lan": lan, "mac": data[26:32].hex(":")}


def received_code(data: bytes) -> IRCode | None:
    if not data or data[0] not in RECEIVED:
        return None
    code = IRCode.parse(data[1:])
    if code.packet[2] != 1:  # SBUS_REPEAT, not status/config/learning data
        return None
    return code


class _Replies(asyncio.DatagramProtocol):
    def __init__(self, ip: str):
        self.ip = ip
        self.queue: asyncio.Queue[bytes | Exception] = asyncio.Queue(maxsize=32)

    def datagram_received(self, data, addr):
        if addr[0] == self.ip and data and not self.queue.full():
            self.queue.put_nowait(bytes(data))

    def error_received(self, exc):
        if not self.queue.full():
            self.queue.put_nowait(exc)


async def resolve_ipv4(host: str, port: int) -> str:
    addresses = await asyncio.get_running_loop().getaddrinfo(
        host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM
    )
    return addresses[0][4][0]


class BinaryClient:
    """Each operation has its own reply socket, excluding stale UDP replies."""
    def __init__(self, ip: str, port: int = 21000):
        self.ip, self.port = ip, int(port)
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def _exchange(self, packet: bytes, expected: int, timeout: float,
                        learning: bool = False) -> bytes:
        async with self._lock:
            protocol = _Replies(self.ip)
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: protocol, local_addr=("0.0.0.0", 0), family=socket.AF_INET
            )
            reply_port = transport.get_extra_info("sockname")[1]
            sequence = 0
            if expected == ACK:
                self._sequence = self._sequence % 255 + 1
                sequence = self._sequence
            completed = False
            try:
                transport.sendto(envelope(packet, reply_port, sequence), (self.ip, self.port))
                async with asyncio.timeout(timeout):
                    while True:
                        data = await protocol.queue.get()
                        if isinstance(data, Exception):
                            raise data
                        kind = data[0]
                        if kind == ACK_BUSY:
                            raise ProtocolError("IRTrans reports that its IR output is busy")
                        if kind in (239, 248):
                            raise ProtocolError(f"IRTrans rejected the binary packet ({kind})")
                        if kind == expected or (expected == ACK and kind == ACK_DUPLICATE):
                            completed = True
                            return data
                        # ACK_WAIT does not mean transmitted; await final ACK.
            finally:
                if learning and not completed:
                    # Same cancel frame used by ReadLearndataLAN timeout handling.
                    transport.sendto(envelope(control(0, b"\0\0"), reply_port),
                                     (self.ip, self.port))
                transport.close()

    async def version(self) -> dict[str, str]:
        return parse_version(await self._exchange(version_command(), VERSION, 5))

    async def learn(self, *, raw: bool = False, timeout: int = 30) -> IRCode:
        response = await self._exchange(learn_command(raw), LEARN, timeout, learning=True)
        if len(response) < 4:
            raise ProtocolError("Incomplete IRTrans learning response")
        # RCV_STATUS_LENGTH and higher encode failures in the high nibble.
        if response[3] & 0xF0 > 0x10:
            raise ProtocolError(f"IRTrans learning failed (status {response[3]})")
        code = IRCode.parse(response[1:])
        code.signature()  # Reject unusable data before storing it.
        return code

    async def send(self, code: IRCode, led: str = "default") -> None:
        await self._exchange(code.for_send(led), ACK, 5)


class ReceiveHub(asyncio.DatagramProtocol):
    """One UDP receiver per HA instance, routing by configured device IP."""
    def __init__(self):
        self.transport = None
        self.handlers: dict[str, Callable[[bytes], None]] = {}
        self.last_error: str | None = None

    async def start(self, port: int = 21000):
        self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: self, local_addr=("0.0.0.0", port), family=socket.AF_INET
        )

    def datagram_received(self, data, addr):
        if callback := self.handlers.get(addr[0]):
            callback(bytes(data))

    def error_received(self, exc):
        self.last_error = str(exc)

    def close(self):
        if self.transport is not None:
            self.transport.close()
            self.transport = None
