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
            elif ir[2] in (18, 82):
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
        self.client=b.BinaryClient('127.0.0.1',self.transport.get_extra_info('sockname')[1])

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


if __name__=='__main__':
    unittest.main()
