"""Persistence, transactional learning and receive-event behavior."""
import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock

from test_binary import code

ROOT=Path(__file__).resolve().parents[1]
package=types.ModuleType('direct_test_package')
package.__path__=[str(ROOT/'custom_components/irtrans')]
sys.modules[package.__name__]=package
# Only the protocol-independent controller's HA services are stubbed here.
for name in ('homeassistant','homeassistant.components','homeassistant.helpers'):
    mod=types.ModuleType(name); mod.__path__=[]; sys.modules[name]=mod
notifications=types.ModuleType('homeassistant.components.persistent_notification')
notifications.async_create=lambda *a,**kw: None
notifications.async_dismiss=lambda *a,**kw: None
sys.modules[notifications.__name__]=notifications
signals=types.ModuleType('homeassistant.helpers.dispatcher')
signals.async_dispatcher_send=lambda *a: None
sys.modules[signals.__name__]=signals


class MemoryStore:
    def __init__(self,*args):
        self.saved=None
        self.failure=False

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self,value):
        if self.failure:
            raise OSError('disk full')
        self.saved=deepcopy(value)


storage=types.ModuleType('homeassistant.helpers.storage'); storage.Store=MemoryStore
sys.modules[storage.__name__]=storage
c=importlib.import_module('direct_test_package.controller')


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events=[]
        self.hass=types.SimpleNamespace(bus=types.SimpleNamespace(
            async_fire=lambda name,data,**kwargs:self.events.append((name,data))))
        self.entry=types.SimpleNamespace(entry_id='entry',data={'port':21000},options={})
        self.controller=c.Controller(self.hass,self.entry,'127.0.0.1')
        self.controller.client.learn=AsyncMock(return_value=c.IRCode.parse(code().packet))
        self.controller.client.send=AsyncMock()

    async def asyncTearDown(self):
        await self.controller.close()

    async def test_learning_persists_and_reload_restores_codes(self):
        await self.controller.learn('tv',['power'])
        saved=deepcopy(self.controller.codes)
        self.assertEqual(self.controller.library,{'tv':['power']})
        self.assertEqual(self.events[-1][0],'irtrans_learned')
        self.controller.codes={}
        await self.controller.load()
        self.assertEqual(self.controller.codes,saved)

    async def test_overwrite_is_transactional_on_timeout(self):
        await self.controller.learn('tv',['power'])
        saved=deepcopy(self.controller.codes)
        self.controller.client.learn.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.controller.learn('tv',['power'])
        self.assertEqual(self.controller.codes,saved)
        self.assertEqual(self.controller.store.saved['codes'],saved)
        self.assertFalse(self.controller.learning)

    async def test_multi_command_failure_does_not_partially_save(self):
        self.controller.client.learn.side_effect=[c.IRCode.parse(code().packet),TimeoutError()]
        with self.assertRaises(TimeoutError):
            await self.controller.learn('tv',['one','two'])
        self.assertEqual(self.controller.codes,{})
        self.assertIsNone(self.controller.store.saved)

    async def test_save_failure_keeps_existing_data(self):
        await self.controller.learn('tv',['power'])
        saved=deepcopy(self.controller.codes)
        self.controller.store.failure=True
        with self.assertRaises(OSError):
            await self.controller.learn('tv',['mute'])
        self.assertEqual(self.controller.codes,saved)
        self.assertFalse(self.controller.learning)

    async def test_unknown_send_is_rejected_before_any_transmission(self):
        await self.controller.learn('tv',['power'])
        with self.assertRaises(ValueError):
            await self.controller.send('tv',['power','missing'])
        self.controller.client.send.assert_not_awaited()

    async def test_repeated_commands_and_num_repeats(self):
        await self.controller.learn('tv',['power'])
        await self.controller.send('tv',['power','power'],repeats=2,delay=0)
        self.assertEqual(self.controller.client.send.await_count,4)

    async def test_delete_persists_and_atomic_unknown_delete(self):
        await self.controller.learn('tv',['power','mute'])
        with self.assertRaises(ValueError):
            await self.controller.delete('tv',['power','missing'])
        self.assertEqual(self.controller.library,{'tv':['mute','power']})
        await self.controller.delete('tv',['power'])
        self.controller.codes={}
        await self.controller.load()
        self.assertEqual(self.controller.library,{'tv':['mute']})

    async def test_receive_matching_and_unknown(self):
        await self.controller.learn('tv',['power'])
        self.controller.sensor_entity_id='sensor.my_actual_irtrans'
        self.events.clear()
        self.controller.receive(bytes((230,))+code(command=1).packet)
        self.assertEqual([name for name,_ in self.events],['irtrans_receive_log','irtrans_received','irtrans_event'])
        event=self.events[-1][1]
        self.assertEqual((event['device'],event['command']),('tv','power'))
        self.assertEqual(event['entity_id'],'sensor.my_actual_irtrans')
        self.assertTrue(self.controller.last_received['matched'])
        self.events.clear()
        self.controller.receive(bytes((230,))+code(data=b'11111',command=1).packet)
        self.assertEqual([name for name,_ in self.events],['irtrans_receive_log','irtrans_received'])
        self.assertFalse(self.controller.last_received['matched'])

    async def test_corrupt_and_learning_receive_suppressed(self):
        self.controller.receive(b'\xe6\x03\0')
        self.controller.learning=True
        self.controller.receive(bytes((230,))+code(command=1).packet)
        self.assertEqual([name for name,_ in self.events],['irtrans_receive_log','irtrans_receive_log'])
        self.assertEqual(self.controller.receive_log_count,2)
        self.assertTrue(self.controller.receive_log[0]['during_learning'])
        self.assertEqual(self.controller.receive_log[1]['kind'],'invalid')

    async def test_corrupt_database_never_silently_discarded(self):
        for value in ([],{}, {'codes':[]},{'codes':{'tv':{'power':{'hex':'00'}}}}):
            self.controller.store.saved=value
            with self.subTest(value=value):
                with self.assertRaises(c.ProtocolError):
                    await self.controller.load()

    async def test_unload_cancels_learning_without_saving(self):
        began=asyncio.Event()
        async def wait(**kwargs):
            began.set()
            await asyncio.Future()
        self.controller.client.learn.side_effect=wait
        task=asyncio.create_task(self.controller.learn('tv',['power']))
        await began.wait()
        await self.controller.close()
        self.assertTrue(task.cancelled())
        self.assertEqual(self.controller.codes,{})
        self.assertFalse(self.controller.learning)
        self.assertFalse(self.controller._tasks)

    async def test_concurrent_learning_and_sending_serialize(self):
        began,finish=asyncio.Event(),asyncio.Event()
        async def learn(**kwargs):
            began.set()
            await finish.wait()
            return c.IRCode.parse(code().packet)
        self.controller.client.learn.side_effect=learn
        first=asyncio.create_task(self.controller.learn('tv',['power']))
        await began.wait()
        second=asyncio.create_task(self.controller.send('tv',['power'],delay=0))
        await asyncio.sleep(0)
        self.controller.client.send.assert_not_awaited()
        finish.set()
        await asyncio.gather(first,second)
        self.controller.client.send.assert_awaited_once()

    async def test_empty_name_and_duplicate_learn_rejected(self):
        for commands in ([],[''],['x','x']):
            with self.assertRaises(ValueError):
                await self.controller.learn('tv',commands)
        self.controller.client.learn.assert_not_awaited()

    async def test_button_rename_keeps_code_and_binding_after_reload(self):
        await self.controller.learn('tv',['power'])
        slot_before=deepcopy(self.controller.slots['1'])
        packet=self.controller.codes['tv']['power']['hex']
        await self.controller.rename_slot(1,'Televize power')
        self.assertEqual(self.controller.slots['1']['device'],slot_before['device'])
        self.assertEqual(self.controller.slots['1']['command'],slot_before['command'])
        self.assertEqual(self.controller.codes['tv']['power']['hex'],packet)
        self.controller.slots={str(i):self.controller.default_slot(i) for i in range(1,9)}
        await self.controller.load()
        self.assertEqual(self.controller.slots['1']['label'],'Televize power')
        self.assertTrue(self.controller.slot_learned(1))

    async def test_label_save_failure_preserves_old_name(self):
        original=deepcopy(self.controller.slots)
        self.controller.store.failure=True
        with self.assertRaises(OSError):
            await self.controller.rename_slot(1,'Changed')
        self.assertEqual(self.controller.slots,original)

    async def test_old_codes_are_offered_as_buttons(self):
        self.controller.store.saved={'codes':{'old_tv':{'power':{'hex':code().packet.hex()}}}}
        await self.controller.load()
        self.assertEqual(self.controller.slots['1']['label'],'old_tv/power')
        self.assertTrue(self.controller.slot_learned(1))

    async def test_named_button_receive_and_send_include_label(self):
        await self.controller.rename_slot(2,'TV mute')
        slot=self.controller.slots['2']
        await self.controller.learn(slot['device'],[slot['command']])
        self.controller.receive(bytes((230,))+code(command=1).packet)
        self.assertEqual(self.controller.last_received['label'],'TV mute')
        await self.controller.send(slot['device'],[slot['command']],source='Button 02')
        self.assertEqual(self.controller.last_sent['label'],'TV mute')
        self.assertEqual(self.controller.last_sent['source'],'Button 02')
        self.assertEqual(self.controller.sent_count,1)

    async def test_receive_log_records_unknown_packets_and_is_bounded(self):
        packet=bytes((230,))+code(command=1).packet
        for _ in range(150):
            self.controller.receive(packet)
        self.assertEqual(self.controller.receive_log_count,150)
        self.assertEqual(len(self.controller.receive_log),100)
        self.assertEqual(self.controller.receive_log[0]['number'],150)
        self.assertEqual(self.controller.receive_log[-1]['number'],51)
        self.assertEqual(self.controller.receive_log[0]['kind'],'unknown')
        self.assertEqual(self.controller.receive_log[0]['raw_hex'],packet.hex())
        self.assertEqual(self.controller.receive_log[0]['source'],'127.0.0.1')

    async def test_receive_log_pauses_when_remote_disabled(self):
        self.controller.enabled=False
        self.controller.receive(bytes((230,))+code(command=1).packet)
        self.assertFalse(self.controller.receive_log)
        self.assertFalse(self.events)
