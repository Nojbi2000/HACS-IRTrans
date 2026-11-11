"""Direct IRTrans LAN: TCP BINA commands and UDP relay reception.

Wire layout and matching are derived from IRTrans 6.10.23 remote.h,
lanio.c, lowlevel.c, fileio.c and server.c. See THIRD-PARTY-NOTICES.txt.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
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

_LOGGER = logging.getLogger(__name__)


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


def learn_command(raw: bool = False, repeat: bool = False) -> bytes:
    # LearnIREx with configured receiver/timeout/tolerance, no persistent changes.
    return control((210 if raw else 146) if repeat else (82 if raw else 18), b"\0\0\0\0")


def envelope(packet: bytes, result_port: int, sequence: int = 0) -> bytes:
    """IRDATA_LAN: command, mode, result_port, 16 uint32 send masks, IRDATA."""
    if not 0 <= result_port <= 65535 or not 0 <= sequence <= 255:
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

    def main_frame(self) -> bytes | None:
        """Decode a bounded 32-bit pulse-distance frame using its own table.

        Only the observed 9 ms / 4.5 ms header, 32 short/long spaces and
        stop pulse are accepted. Optional suffixes must be valid 9 ms /
        2.25 ms repeat frames. Payload bytes are never masked or corrected.
        Keep all native bytes unchanged for replay; this key is receive-only.
        """
        if self.mode not in (0, 1):
            return None
        symbols = self.signature()
        count = self.packet[41]
        if not 1 <= count <= 8 or len(symbols) < 34:
            return None
        timings = []
        for symbol in symbols:
            if symbol == ord("S") and self.mode & 1:
                index = 0
            elif ord("0") <= symbol <= ord("7"):
                index = symbol - ord("0") + (1 if self.mode & 1 else 0)
            else:
                return None
            if index >= count:
                return None
            pulse = struct.unpack_from("<h", self.packet, 25 + 2 * index)[0] * 8
            pause = struct.unpack_from("<h", self.packet, 9 + 2 * index)[0] * 8
            timings.append((pulse, pause))
        if not (8000 <= timings[0][0] <= 10000 and 3500 <= timings[0][1] <= 5500):
            return None
        bits = []
        for pulse, pause in timings[1:33]:
            if not 350 <= pulse <= 850:
                return None
            if 350 <= pause <= 850:
                bits.append(0)
            elif 1200 <= pause <= 2000:
                bits.append(1)
            else:
                return None
        if not 350 <= timings[33][0] <= 850:
            return None
        suffix = timings[34:]
        if len(suffix) % 2:
            return None
        for header, stop in zip(suffix[::2], suffix[1::2]):
            if not (8000 <= header[0] <= 10000 and 1700 <= header[1] <= 2800
                    and 350 <= stop[0] <= 850):
                return None
        if not 30 <= self.packet[7] <= 45:
            return None
        return bytes(sum(bits[base + bit] << bit for bit in range(8))
                     for base in range(0, 32, 8))

    @property
    def family(self) -> int:
        return self.mode & 159 if self.mode & 128 else self.mode & 28

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(bytes((self.family,)) + self.signature()).hexdigest()[:24]

    def matches(self, other: IRCode) -> bool:
        own_main, received_main = self.main_frame(), other.main_frame()
        if own_main is not None and received_main is not None:
            return own_main == received_main
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
    """Use fresh binary TCP sessions; retain explicit UDP mode for compatibility."""
    def __init__(self, ip: str, port: int = 21000, *, transport: str = "tcp"):
        if transport not in ("tcp", "udp"):
            raise ValueError("Transport must be tcp or udp")
        self.ip, self.port = ip, int(port)
        self.transport = transport
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def _exchange(self, packet: bytes, expected: int, timeout: float,
                        learning: bool = False) -> bytes:
        async with self._lock:
            if self.transport == "tcp":
                return await self._exchange_tcp(packet, expected, timeout, learning)
            return await self._exchange_udp(packet, expected, timeout, learning)

    async def _exchange_tcp(self, packet: bytes, expected: int, timeout: float,
                            learning: bool = False) -> bytes:
        """BINA handshake and framed status replies on a dedicated TCP connection.

        IRTransLanSend/open_irtrans_tcp in the official source use BINA and
        wait 500 ms after initialization. No reply UDP port is requested here.
        """
        writer = None
        completed = False
        reply_types = []
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_connection(self.ip, self.port)
                writer.write(b"BINA")
                await writer.drain()
                await asyncio.sleep(.5)
                writer.write(envelope(packet, 0))
                await writer.drain()
                response = await self._read_tcp_reply(reader, expected, reply_types)
                completed = True
                return response
        except TimeoutError as exc:
            received = ",".join(map(str, reply_types)) or "none"
            raise TimeoutError(
                f"No binary TCP reply type {expected} from {self.ip}:{self.port} "
                f"within {timeout:g}s (received types: {received}). Check the "
                "IRTrans address and TCP access rules."
            ) from exc
        except asyncio.IncompleteReadError as exc:
            raise ProtocolError("IRTrans closed the binary TCP connection before a complete reply") from exc
        finally:
            if writer is not None:
                if learning and not completed and not writer.is_closing():
                    writer.write(envelope(control(0, b"\0\0"), 0))
                writer.close()
                try:
                    async with asyncio.timeout(2):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass

    async def _read_tcp_reply(self, reader, expected, reply_types):
        while True:
            kind = (await reader.readexactly(1))[0]
            if len(reply_types) < 20:
                reply_types.append(kind)
            if kind in (ACK, ACK_WAIT, ACK_DUPLICATE, ACK_BUSY):
                response = bytes((kind,))
            elif 230 <= kind <= 253:
                # Binary status packets contain netcommand, then IRDATA
                # length (which includes the length byte itself).
                length = (await reader.readexactly(1))[0]
                if length < 1:
                    raise ProtocolError("Invalid binary TCP status length")
                response = bytes((kind, length)) + await reader.readexactly(length - 1)
            else:
                raise ProtocolError(f"Unexpected binary TCP status type {kind}")
            _LOGGER.debug("IRTrans binary TCP reply type %s, %s bytes", kind, len(response))
            if kind == ACK_BUSY:
                raise ProtocolError("IRTrans reports that its IR output is busy")
            if kind in (239, 248):
                raise ProtocolError(f"IRTrans rejected the binary packet ({kind})")
            if kind == expected or (expected == ACK and kind == ACK_DUPLICATE):
                return response
            # Relay frames may precede a requested reply. Read each whole
            # frame and wait for the matching type, never just one read().

    async def send_hold(self, initial: IRCode, repeat: IRCode, *, seconds: float,
                        interval: float = .11, led: str = "default") -> int:
        """One BINA session: initial code once, then the learned repeat code.

        Interval is a minimum between request starts; ACK latency may extend it.
        Never replay automatically after a timeout or reconnect during a hold.
        """
        if not .1 <= seconds <= 10 or not .03 <= interval <= 1:
            raise ValueError("Hold must be 0.1–10 seconds; interval 0.03–1 second")
        if self.transport != "tcp":
            raise ProtocolError("Hold transmission requires binary TCP")
        first, following = initial.for_send(led), repeat.for_send(led)
        writer = None
        try:
            async with asyncio.timeout(seconds + 6):
                reader, writer = await asyncio.open_connection(self.ip, self.port)
                writer.write(b"BINA")
                await writer.drain()
                await asyncio.sleep(.5)
                clock = asyncio.get_running_loop()
                start = clock.time()
                count = 0
                while True:
                    if count and clock.time() >= start + seconds:
                        return count
                    sent_at = clock.time()
                    writer.write(envelope(first if count == 0 else following, 0))
                    await writer.drain()
                    await self._read_tcp_reply(reader, ACK, [])
                    count += 1
                    due = max(sent_at + interval, clock.time())
                    if due >= start + seconds:
                        return count
                    await asyncio.sleep(max(0, due - clock.time()))
        except asyncio.IncompleteReadError as exc:
            raise ProtocolError("IRTrans closed the hold connection before acknowledgement") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(2):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass

    async def _exchange_udp(self, packet: bytes, expected: int, timeout: float,
                        learning: bool = False) -> bytes:
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
        reply_types = []
        _LOGGER.debug("IRTrans UDP request to %s:%s, expected type %s, reply port %s",
                      self.ip, self.port, expected, reply_port)
        try:
            transport.sendto(envelope(packet, reply_port, sequence), (self.ip, self.port))
            async with asyncio.timeout(timeout):
                while True:
                    data = await protocol.queue.get()
                    if isinstance(data, Exception):
                        raise data
                    kind = data[0]
                    if len(reply_types) < 20:
                        reply_types.append(kind)
                    _LOGGER.debug("IRTrans UDP reply type %s, %s bytes", kind, len(data))
                    if kind == ACK_BUSY:
                        raise ProtocolError("IRTrans reports that its IR output is busy")
                    if kind in (239, 248):
                        raise ProtocolError(f"IRTrans rejected the binary packet ({kind})")
                    if kind == expected or (expected == ACK and kind == ACK_DUPLICATE):
                        completed = True
                        return data
                    # ACK_WAIT does not mean transmitted; await final ACK.
        except TimeoutError as exc:
            received = ",".join(map(str, reply_types)) or "none"
            raise TimeoutError(
                f"No UDP reply type {expected} from {self.ip}:{self.port} within "
                f"{timeout:g}s (HA reply port {reply_port}; received types: {received}). "
                "Check the IRTrans address, IP access rules and bidirectional UDP connectivity."
            ) from exc
        finally:
            if learning and not completed:
                # Same cancel frame used by ReadLearndataLAN timeout handling.
                transport.sendto(envelope(control(0, b"\0\0"), reply_port),
                                 (self.ip, self.port))
            transport.close()

    async def version(self) -> dict[str, str]:
        return parse_version(await self._exchange(version_command(), VERSION, 5))

    async def learn(self, *, raw: bool = False, timeout: int = 30, repeat: bool = False) -> IRCode:
        response = await self._exchange(learn_command(raw, repeat), LEARN, timeout, learning=True)
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
