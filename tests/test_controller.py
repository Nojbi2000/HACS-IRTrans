"""Persistence, transactional learning and receive-event behavior."""
import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock

from test_binary import code, distance_code, short_repeat, apple_hold_variant, noisy_repeat_train

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
        c._SAMPLE_RELEASE_SECONDS = 0
        self.events=[]
        self.hass=types.SimpleNamespace(bus=types.SimpleNamespace(
            async_fire=lambda name,data,**kwargs:self.events.append((name,data))))
        self.entry=types.SimpleNamespace(entry_id='entry',data={'port':21000},options={})
        self.controller=c.Controller(self.hass,self.entry,'127.0.0.1')
        self.controller.client.learn=AsyncMock(return_value=c.IRCode.parse(code().packet))
        self.controller.client.send=AsyncMock()

    async def asyncTearDown(self):
        await self.controller.close()

    async def test_remote_groups_preserve_hidden_slots_and_separate_code_keys(self):
        await self.controller.rename_slot(2, 'Original mute')
        await self.controller.learn('buttons', ['button_02'])
        saved = deepcopy(self.controller.store.saved)
        self.entry.options = {'remotes': [
            {'id': 'main', 'name': 'TV', 'base': 0, 'button_count': 1},
            {'id': 'amp', 'name': 'Amplifier', 'base': 1000, 'button_count': 4}]}
        other = c.Controller(self.hass, self.entry, '127.0.0.1')
        other.store.saved = saved
        await other.load()
        self.assertNotIn(2, other.slot_indices)
        self.assertEqual(other.slots['2']['label'], 'Original mute')
        self.assertTrue(other.slot_learned(2))
        self.assertEqual(other.slots['1001']['device'], 'remote_amp')
        self.assertEqual(other.default_slot(1)['device'], 'buttons')
        await other.rename_slot(1001, 'Amp power')
        self.entry.options['remotes'][0]['button_count'] = 12
        restored = c.Controller(self.hass, self.entry, '127.0.0.1')
        restored.store.saved = deepcopy(other.store.saved)
        await restored.load()
        self.assertIn(2, restored.slot_indices)
        self.assertEqual(restored.slots['1001']['label'], 'Amp power')
        self.assertTrue(restored.slot_learned(2))

    async def test_repeat_learning_is_transactional_and_persists(self):
        await self.controller.learn('tv', ['volume'])
        original = deepcopy(self.controller.codes)
        self.controller.client.learn.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.controller.learn_repeat('tv', 'volume')
        self.assertEqual(self.controller.codes, original)
        self.controller.client.learn.side_effect = None
        self.controller.client.learn.return_value = c.IRCode.parse(code(data=b'S01').packet)
        await self.controller.learn_repeat('tv', 'volume')
        self.controller.client.learn.assert_awaited_with(raw=False,timeout=30,repeat=True)
        self.assertEqual(self.controller.codes['tv']['volume']['hex'], original['tv']['volume']['hex'])
        await self.controller.load()
        self.assertEqual(self.controller.codes['tv']['volume']['repeat_hex'], code(data=b'S01').packet.hex())

    async def test_hold_requires_repeat_and_routes_separate_codes(self):
        await self.controller.learn('tv', ['volume'])
        self.controller.client.send_hold = AsyncMock(return_value=3)
        with self.assertRaises(ValueError):
            await self.controller.send('tv', ['volume'], hold_secs=.2)
        self.controller.client.send_hold.assert_not_awaited()
        self.controller.client.learn.return_value = c.IRCode.parse(code(data=b'S01').packet)
        await self.controller.learn_repeat('tv', 'volume')
        await self.controller.send('tv', ['volume'], hold_secs=.2)
        args=self.controller.client.send_hold.await_args
        self.assertNotEqual(args.args[0].packet,args.args[1].packet)
        self.assertEqual(args.kwargs['seconds'],.2)
        self.assertEqual(self.controller.last_sent['frame_count'],3)

    async def test_repeat_receive_requires_recent_initial_command(self):
        await self.controller.learn('tv', ['volume'])
        self.controller.client.learn.return_value = c.IRCode.parse(code(data=b'S01').packet)
        await self.controller.learn_repeat('tv', 'volume')
        repeat=bytes((230,))+code(data=b'S01',command=1).packet
        self.controller.receive(repeat)
        self.assertFalse(self.controller.last_received['matched'])
        self.controller.receive(bytes((230,))+code(command=1).packet)
        self.assertFalse(self.controller.last_received['is_repeat'])
        self.controller.receive(repeat)
        self.assertTrue(self.controller.last_received['is_repeat'])
        self.assertEqual(self.controller.last_received['command'],'volume')
        self.assertEqual(self.controller.last_received['repeat_count'],1)
        self.assertIn('repeat 1',self.controller.receive_log[0]['message'])
        self.controller.receive(bytes((230,))+code(data=b'S11111',command=1).packet)
        self.controller.receive(repeat)
        self.assertFalse(self.controller.last_received['matched'])
        self.controller._receive_chain=(c.time.monotonic()-1,[('tv','volume')],1)
        self.controller.receive(repeat)
        self.assertFalse(self.controller.last_received['matched'])

    async def test_first_press_matches_without_receive_history(self):
        self.controller.client.learn.return_value=c.IRCode.parse(distance_code(repeat=True).packet)
        await self.controller.learn('tv',['menu'])
        self.assertIsNone(self.controller._receive_chain)
        self.controller.receive(bytes((230,))+distance_code(reindex=True,split=True).packet)
        self.assertEqual(self.controller.last_received['command'],'menu')
        self.assertFalse(self.controller.last_received['is_repeat'])
        self.assertEqual(self.controller.receive_log[0]['kind'],'known')
        self.assertEqual(self.events[-1][0],'irtrans_event')

    async def test_three_short_samples_match_before_commit(self):
        self.controller.client.learn.side_effect=[c.IRCode.parse(frame.packet) for frame in (
            distance_code(repeat=True),distance_code(),distance_code(reindex=True,split=True))]
        await self.controller.learn('tv',['menu'])
        record=self.controller.codes['tv']['menu']
        self.assertEqual(record['sample_count'],3)
        self.assertEqual(len(record['variants']),3)
        self.assertEqual(self.controller.client.learn.await_count,3)
        self.assertEqual(len(c.IRCode.from_hex(record['hex']).data),34)

    async def test_inconsistent_short_samples_preserve_old_record(self):
        await self.controller.learn('tv',['menu'])
        saved=deepcopy(self.controller.codes)
        self.controller.client.learn.side_effect=[c.IRCode.parse(frame.packet) for frame in (
            distance_code(),distance_code(),distance_code(bytes.fromhex('aa5504a5')))]
        with self.assertRaises(c.ProtocolError):
            await self.controller.learn('tv',['menu'])
        self.assertEqual(self.controller.codes,saved)

    async def test_three_repeat_samples_commit_variants_atomically(self):
        await self.controller.learn('tv',['menu'])
        self.controller.client.learn.reset_mock()
        self.controller.client.learn.side_effect=[c.IRCode.parse(frame.packet) for frame in (
            short_repeat(),short_repeat(start=False),short_repeat())]
        await self.controller.learn_repeat('tv','menu')
        record=self.controller.codes['tv']['menu']
        self.assertEqual(record['repeat_sample_count'],3)
        self.assertEqual(len(record['repeat_variants']),2)
        self.assertEqual(self.controller.client.learn.await_count,3)
        await self.controller.load()
        self.assertEqual(self.controller.codes['tv']['menu'],record)

    async def test_inconsistent_repeat_samples_preserve_old_record(self):
        self.controller.client.learn.return_value=c.IRCode.parse(distance_code().packet)
        await self.controller.learn('tv',['menu'])
        saved=deepcopy(self.controller.codes)
        self.controller.client.learn.side_effect=[c.IRCode.parse(frame.packet) for frame in (
            distance_code(),distance_code(bytes.fromhex('aa5504a5')),short_repeat())]
        with self.assertRaises(c.ProtocolError):
            await self.controller.learn_repeat('tv','menu')
        self.assertEqual(self.controller.codes,saved)

    async def test_physical_hold_variant_follows_only_matching_recent_press(self):
        self.controller.client.learn.return_value=c.IRCode.parse(distance_code(bytes.fromhex('ee8702a5')).packet)
        await self.controller.learn('tv',['menu'])
        held=bytes((230,))+apple_hold_variant().packet
        self.controller.receive(held)
        self.assertFalse(self.controller.last_received['matched'])
        self.controller.receive(bytes((230,))+distance_code(bytes.fromhex('ee8702a5')).packet)
        self.controller.receive(held)
        self.assertEqual(self.controller.last_received['command'],'menu')
        self.assertTrue(self.controller.last_received['is_repeat'])
        self.assertEqual(self.controller.last_received['repeat_count'],1)
        self.controller.receive(bytes((230,))+apple_hold_variant(command=4).packet)
        self.assertFalse(self.controller.last_received['matched'])
        self.controller.receive(held)
        self.assertFalse(self.controller.last_received['matched'])

    async def test_short_repeat_can_follow_main_without_optional_repeat_learning(self):
        self.controller.client.learn.return_value=c.IRCode.parse(distance_code().packet)
        await self.controller.learn('tv',['menu'])
        self.controller.receive(bytes((230,))+short_repeat().packet)
        self.assertFalse(self.controller.last_received['matched'])
        self.controller.receive(bytes((230,))+distance_code().packet)
        self.controller.receive(bytes((230,))+short_repeat(start=False).packet)
        self.assertEqual(self.controller.last_received['command'],'menu')
        self.assertTrue(self.controller.last_received['is_repeat'])
        self.controller._receive_chain=(c.time.monotonic()-1,[('tv','menu')],1)
        self.controller.receive(bytes((230,))+short_repeat().packet)
        self.assertFalse(self.controller.last_received['matched'])

    async def test_noisy_repeat_train_keeps_following_short_repeat_named(self):
        self.controller.client.learn.return_value=c.IRCode.parse(distance_code().packet)
        await self.controller.learn('tv',['menu'])
        train=bytes((230,))+noisy_repeat_train().packet
        self.controller.receive(train)
        self.assertFalse(self.controller.last_received['matched'])
        self.controller.receive(bytes((230,))+distance_code().packet)
        self.controller.receive(train)
        self.assertEqual(self.controller.last_received['command'],'menu')
        self.assertTrue(self.controller.last_received['is_repeat'])
        self.controller.receive(bytes((230,))+short_repeat().packet)
        self.assertEqual(self.controller.last_received['command'],'menu')
        self.assertEqual(self.controller.last_received['repeat_count'],2)

    async def test_noisy_combined_main_can_start_a_fresh_press(self):
        self.controller.client.learn.return_value=c.IRCode.parse(distance_code().packet)
        await self.controller.learn('tv',['menu'])
        self.controller.receive(bytes((230,))+noisy_repeat_train(main=True).packet)
        self.assertEqual(self.controller.last_received['command'],'menu')
        self.assertFalse(self.controller.last_received['is_repeat'])

    async def test_learning_progress_reports_three_samples_and_release_steps(self):
        seen=[]
        self.controller.notify=lambda:seen.append(deepcopy(self.controller.learning_progress))
        await self.controller.learn('buttons',['button_01'])
        self.assertEqual([p['sample'] for p in seen if p['phase']=='waiting'],[1,2,3])
        self.assertEqual([p['sample'] for p in seen if p['phase']=='release'],[2,3])
        self.assertEqual(self.controller.learning_progress['phase'],'done')
        self.assertIn('3/3',self.controller.learning_progress['message'])
        self.assertFalse(self.controller.learning)

    async def test_failed_learning_keeps_a_visible_error_status(self):
        self.controller.client.learn.side_effect=TimeoutError('No response')
        with self.assertRaises(TimeoutError):
            await self.controller.learn('buttons',['button_01'])
        self.assertEqual(self.controller.learning_progress['phase'],'error')
        self.assertIn('No response',self.controller.learning_progress['message'])
        self.assertFalse(self.controller.learning)

    async def test_repeat_progress_identifies_repeat_and_completes(self):
        await self.controller.learn('buttons',['button_01'])
        seen=[]
        self.controller.notify=lambda:seen.append(deepcopy(self.controller.learning_progress))
        await self.controller.learn_repeat('buttons','button_01')
        self.assertEqual([p['sample'] for p in seen if p['phase']=='waiting'],[1,2,3])
        self.assertTrue(all(p['kind']=='repeat' for p in seen if p['phase'] in ('waiting','release')))
        self.assertEqual(self.controller.learning_progress['phase'],'done')

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


class RemoteGroupTests(unittest.TestCase):
    def test_add_group_preserves_legacy_and_other_options(self):
        append=importlib.import_module('direct_test_package.groups').append_remote
        options={'button_count':12,'led':'both','hold_secs':2}
        before=deepcopy(options)
        groups,group=append(options,' Television ',4,'a'*32)
        self.assertEqual(options,before)
        self.assertEqual(groups[0],{'id':'main','name':'Remote 01','base':0,'button_count':12})
        self.assertEqual(group['name'],'Television')
        self.assertEqual(group['base'],1000)

    def test_add_group_request_is_idempotent(self):
        append=importlib.import_module('direct_test_package.groups').append_remote
        groups,group=append({},'TV',8,'a'*32)
        again,existing=append({'remotes':groups},'TV',8,'a'*32)
        self.assertEqual(groups,again)
        self.assertEqual(group,existing)
        with self.assertRaises(ValueError):append({'remotes':groups},'Other name',8,'a'*32)

    def test_add_group_validates_without_mutating_options(self):
        append=importlib.import_module('direct_test_package.groups').append_remote
        for name,count,request in [(' ',8,'a'*32),('x'*101,8,'a'*32),('TV',33,'a'*32),
                                   ('TV',0,'a'*32),('TV',True,'a'*32),('TV',1.5,'a'*32),('TV',8,'bad')]:
            with self.subTest(name=name,count=count,request=request):
                with self.assertRaises(ValueError):append({},name,count,request)

    def test_add_multiple_groups_allocates_stable_bases(self):
        append=importlib.import_module('direct_test_package.groups').append_remote
        groups,first=append({},'TV',8,'a'*32)
        groups,second=append({'remotes':groups},'Amp',2,'b'*32)
        self.assertEqual([g['base'] for g in groups],[0,1000,2000])
        self.assertNotEqual(first['id'],second['id'])
