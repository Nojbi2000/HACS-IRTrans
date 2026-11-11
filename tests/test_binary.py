"""Protocol tests with C-header reference vectors and a real fake UDP device."""
import asyncio
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('irtrans_binary_test', ROOT / 'custom_components/irtrans/binary.py')
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


def code(data=b'S010101001', mode=1, command=22):
    offset = 84 if mode == 128 else (54 if mode == 131 else (9 if mode & 16 and not mode & 128 else 44))
    packet = bytearray(offset + len(data))
    packet[0], packet[2], packet[6], packet[7], packet[8] = len(packet), command, len(data), 38, mode
    if offset >= 44:
        packet[9:13] = struct.pack('<hh', 1200, 2400)
        packet[25:29] = struct.pack('<hh', 600, 600)
        packet[offset-3:offset] = bytes((2, 1, 40))
    packet[offset:] = data
    return b.IRCode.parse(b.checksum(packet))


def version():
    packet = bytearray(78)
    packet[:2] = bytes((231, 77))
    packet[2:10] = b'E6.08.10'
    packet[18:26] = b'L1.10.30'
    packet[26:32] = b'\x01\x02\x03\x04\x05\x06'
    return bytes(packet)


class FakeDevice(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.requests = []
        self.reply = code()
        self.behavior = 'normal'
        self.errors = []
        self.sent = []
        self.ports = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            assert len(data) >= 71 and data[0] == 200
            assert not any(data[4:68])
            ir = data[68:]
            assert ir[0] == len(ir) and ir[1] == sum(ir[2:]) % 256
            port = struct.unpack_from('<H', data, 2)[0]
            self.ports.append(port)
            target = addr[0], port
            self.requests.append(ir)
            if ir[2] == 2:
                if self.behavior != 'silent':
                    self.transport.sendto(version(), target)
            elif ir[2] in (18, 82, 146, 210):
                if self.behavior == 'learn_error':
                    bad = bytearray(self.reply.packet)
                    bad[2] = 32
                    self.transport.sendto(bytes((242,)) + bad, target)
                elif self.behavior != 'silent':
                    self.transport.sendto(bytes((242,)) + self.reply.packet, target)
            elif ir[2] == 6:
                self.sent.append(ir)
                if self.behavior == 'busy':
                    self.transport.sendto(bytes((215,)), target)
                elif self.behavior != 'silent':
                    self.transport.sendto(bytes((213,)), target)
                    self.transport.sendto(bytes((212,)), target)
            else:
                assert ir == b.control(0, b'\0\0')
        except Exception as exc:
            self.errors.append(exc)


class CodecTests(unittest.TestCase):
    def test_c_reference_vectors(self):
        lines = (ROOT / 'tests/reference-frames.txt').read_text().splitlines()
        packets = [b.version_command(), b.learn_command(), b.learn_command(True), b.control(0, b'\0\0')]
        for index, packet in enumerate(packets):
            self.assertEqual(b.envelope(packet, 31000).hex(), lines[index])
        self.assertEqual(lines[4], '68 44 41 78')

    def test_repeat_learning_opcodes_from_manufacturer_header(self):
        self.assertEqual(b.learn_command(repeat=True), b.control(146, bytes(4)))
        self.assertEqual(b.learn_command(raw=True, repeat=True), b.control(210, bytes(4)))

    def test_send_keeps_timings_and_replaces_routing(self):
        original = code()
        sent = original.for_send('external')
        self.assertEqual(sent[2:6], bytes((6, 2, 255, 255)))
        self.assertEqual(sent[6:], original.packet[6:])
        self.assertEqual(sent[1], sum(sent[2:]) % 256)

    def test_modes_roundtrip(self):
        for mode in (0, 1, 4, 8, 16, 128, 129, 130, 131, 132, 133, 134):
            with self.subTest(mode=mode):
                original = code(mode=mode)
                self.assertEqual(b.IRCode.from_hex(original.packet.hex()), original)
                self.assertTrue(original.matches(original))

    def test_bad_lengths_and_modes(self):
        for packet in (b'', bytes(10), b'\x08'+bytes(7),
                       code().packet[:-1], code().packet+b'xx'):
            with self.subTest(packet=packet):
                with self.assertRaises(b.ProtocolError):
                    b.IRCode.parse(packet)
        packet=bytearray(code().packet); packet[8]=150
        with self.assertRaises(b.ProtocolError):
            b.IRCode.parse(packet)

    def test_fingerprint_ignores_volatile_timing_measurements(self):
        first=code(); changed=bytearray(first.packet)
        changed[9]=95; changed[7]=39
        second=b.IRCode.parse(bytes(changed))
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertTrue(first.matches(second))

    def test_different_commands_and_families_do_not_match(self):
        self.assertFalse(code(b'0101').matches(code(b'0100')))
        self.assertFalse(code(b'0101',4).matches(code(b'0101',8)))

    def test_toggle_normalization(self):
        self.assertTrue(code(b'001100',4).matches(code(b'000100',4)))
        self.assertTrue(code(b'10000111',8).matches(code(b'10000011',8)))

    def test_calibration_prefix_and_packed_codes(self):
        self.assertTrue(code(bytes((3,22,33))+b'S0101').matches(code(b'S0101')))
        self.assertTrue(code(b'Xl0101').matches(code(b'0101')))
        self.assertTrue(code(bytes((0x90,0x10))).matches(code(b'0101')))

    def test_raw_tolerance(self):
        self.assertTrue(code(bytes((100,110,120)),16).matches(code(bytes((105,115,125)),16)))
        self.assertFalse(code(bytes((100,110,120)),16).matches(code(bytes((150,160,170)),16)))
        self.assertFalse(code(b'abc',16).matches(code(b'ab',16)))

    def test_receive_type_and_command_filter(self):
        frame=code(command=1).packet
        for kind in (230,246,247):
            self.assertIsNotNone(b.received_code(bytes((kind,))+frame))
        self.assertIsNone(b.received_code(bytes((242,))+frame))
        self.assertIsNone(b.received_code(bytes((230,))+code(command=22).packet))

    def test_parse_version(self):
        self.assertEqual(b.parse_version(version()), {'ir':'E6.08.10','lan':'L1.10.30','mac':'01:02:03:04:05:06'})
        with self.assertRaises(b.ProtocolError):
            b.parse_version(version()[:-1])


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake=FakeDevice()
        self.transport,_=await asyncio.get_running_loop().create_datagram_endpoint(
            lambda:self.fake, local_addr=('127.0.0.1',0))
        self.client=b.BinaryClient('127.0.0.1',self.transport.get_extra_info('sockname')[1],transport='udp')

    async def asyncTearDown(self):
        self.transport.close()
        await asyncio.sleep(0)
        self.assertEqual(self.fake.errors,[])

    async def test_probe_learn_replay(self):
        self.assertEqual((await self.client.version())['lan'],'L1.10.30')
        learned=await self.client.learn()
        await self.client.send(learned,'both')
        self.assertEqual(self.fake.sent[0][3],3)
        self.assertEqual(self.fake.sent[0][6:],learned.packet[6:])
        self.assertEqual(len(set(self.fake.ports)),3)

    async def test_raw_learning_command(self):
        await self.client.learn(raw=True)
        self.assertEqual(self.fake.requests[-1][2],82)

    async def test_device_error_and_busy(self):
        self.fake.behavior='learn_error'
        with self.assertRaises(b.ProtocolError):
            await self.client.learn()
        self.fake.behavior='busy'
        with self.assertRaisesRegex(b.ProtocolError,'busy'):
            await self.client.send(code())

    async def test_timeout_sends_cancel_and_closes_exchange(self):
        self.fake.behavior='silent'
        with self.assertRaises(TimeoutError):
            await self.client._exchange(b.learn_command(),242,.02,learning=True)
        await asyncio.sleep(.01)
        self.assertEqual(self.fake.requests[-1],b.control(0,b'\0\0'))
        self.fake.behavior='normal'
        self.assertEqual((await self.client.version())['ir'],'E6.08.10')
        self.assertNotEqual(self.fake.ports[-1],self.fake.ports[0])

    async def test_listener_routes_only_configured_ip(self):
        hub=b.ReceiveHub()
        await hub.start(0)
        events=[]
        hub.handlers['127.0.0.1']=events.append
        try:
            port=hub.transport.get_extra_info('sockname')[1]
            packet=bytes((230,))+code(command=1).packet
            self.transport.sendto(packet,('127.0.0.1',port))
            await asyncio.sleep(.02)
            self.assertEqual(events,[packet])
            hub.datagram_received(packet,('192.0.2.10',21000))
            self.assertEqual(len(events),1)
        finally:
            hub.close()


class FakeTCPDevice(FakeDevice):
    """TCP BINA emulator, also usable as an independent UDP relay sender."""
    def __init__(self):
        super().__init__()
        self.server = None
        self.tasks = []
        self.connections = 0

    async def start(self):
        def accept(reader, writer):
            self.tasks.append(asyncio.create_task(self.handle(reader, writer)))
        self.server = await asyncio.start_server(accept, '127.0.0.1', 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        await asyncio.gather(*self.tasks)

    async def handle(self, reader, writer):
        self.connections += 1
        try:
            assert await reader.readexactly(4) == b'BINA'
            while True:
                header = await reader.readexactly(69)
                assert header[0] == 200 and not any(header[1:68])
                size = header[68]
                assert size >= 3
                ir = bytes((size,)) + await reader.readexactly(size-1)
                assert ir[1] == sum(ir[2:]) % 256
                self.requests.append(ir)
                if self.behavior == 'closed':
                    return
                if ir[2] == 2:
                    response = bytes((230,))+code(command=1).packet+version()
                elif ir[2] in (18,82,146,210):
                    if self.behavior == 'silent':
                        continue
                    learned = bytearray(self.reply.packet)
                    if self.behavior == 'learn_error': learned[2] = 32
                    response = bytes((242,))+learned
                elif ir[2] == 6:
                    self.sent.append(ir)
                    response = bytes((215,)) if self.behavior == 'busy' else bytes((213,212))
                else:
                    assert ir == b.control(0,b'\0\0')
                    continue
                if self.behavior == 'silent': continue
                writer.write(response[:3])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(response[3:])
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        except Exception as exc:
            self.errors.append(exc)
        finally:
            writer.close()
            await writer.wait_closed()


class TCPNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_hold_uses_one_session_and_distinct_repeat_frames(self):
        initial, repeat = code(data=b'S01010'), code(data=b'S01')
        connections = self.fake.connections
        count = await self.client.send_hold(initial, repeat, seconds=.18, interval=.05)
        self.assertGreaterEqual(count, 2)
        self.assertEqual(self.fake.connections, connections + 1)
        self.assertEqual(self.fake.sent, [initial.for_send()] + [repeat.for_send()] * (count - 1))
        self.assertFalse(self.fake.errors)

    async def test_hold_busy_does_not_retry_or_reconnect(self):
        self.fake.behavior = 'busy'
        connections = self.fake.connections
        with self.assertRaises(b.ProtocolError):
            await self.client.send_hold(code(), code(), seconds=.2)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(self.fake.connections, connections + 1)

    async def test_repeat_learning_over_tcp(self):
        await self.client.learn(repeat=True)
        self.assertEqual(self.fake.requests[-1][2],146)
        await self.client.learn(raw=True,repeat=True)
        self.assertEqual(self.fake.requests[-1][2],210)


    async def asyncSetUp(self):
        self.fake=FakeTCPDevice()
        self.client=b.BinaryClient('127.0.0.1',await self.fake.start())

    async def asyncTearDown(self):
        await self.fake.close()
        self.assertEqual(self.fake.errors,[])

    async def test_version_learn_send_on_fresh_tcp_sessions(self):
        self.assertEqual((await self.client.version())['lan'],'L1.10.30')
        learned=await self.client.learn()
        await self.client.send(learned)
        self.assertEqual(self.fake.connections,3)
        self.assertEqual(self.fake.sent[0][6:],learned.packet[6:])

    async def test_tcp_busy_and_learning_error(self):
        self.fake.behavior='busy'
        with self.assertRaisesRegex(b.ProtocolError,'busy'):
            await self.client.send(code())
        self.fake.behavior='learn_error'
        with self.assertRaisesRegex(b.ProtocolError,'learning failed'):
            await self.client.learn()

    async def test_tcp_closed_connection_is_clear_error(self):
        self.fake.behavior='closed'
        with self.assertRaisesRegex(b.ProtocolError,'closed the binary TCP'):
            await self.client.version()

    async def test_tcp_timeout_cancels_learning(self):
        self.fake.behavior='silent'
        with self.assertRaisesRegex(TimeoutError,'No binary TCP reply'):
            await self.client._exchange(b.learn_command(),242,.7,learning=True)
        await asyncio.sleep(.02)
        self.assertEqual(self.fake.requests[-1],b.control(0,b'\0\0'))


if __name__=='__main__':
    unittest.main()
