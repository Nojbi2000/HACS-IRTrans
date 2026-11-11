"""Real HA 2026.11.1 integration test with fake TCP hardware and UDP reception, no external network.

Run inside official HA container with this extracted folder mounted at /work:
python /work/tests/ha_smoke.py
"""
import asyncio
from datetime import datetime, timezone
import json

from pathlib import Path
import shutil
import sys
import tempfile
from types import MappingProxyType

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_binary import FakeTCPDevice, code

from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.core import HomeAssistant, Context
from homeassistant.setup import async_setup_component
from homeassistant.helpers import frame, entity_registry, device_registry, area_registry


async def main():
    with tempfile.TemporaryDirectory(prefix='irtrans-ha-test-') as config_dir:
        shutil.copytree(Path(__file__).resolve().parents[1]/'custom_components',
                        Path(config_dir)/'custom_components')
        hass=HomeAssistant(config_dir)
        loader.async_setup(hass)
        loaded = await bootstrap.async_from_config_dict({
            "homeassistant": {"latitude": 0, "longitude": 0, "elevation": 0,
                              "unit_system": "metric", "time_zone": "UTC", "country": "CZ"},
            "persistent_notification": {},
        }, hass=hass)
        assert loaded is not None
        fake=FakeTCPDevice()
        transport,_=await asyncio.get_running_loop().create_datagram_endpoint(
            lambda:fake, local_addr=('127.0.0.1',0))
        device_port=await fake.start()
        # Reserve a free port for the persistent receiver independently.
        holder,_=await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,local_addr=('127.0.0.1',0))
        receive_port=holder.get_extra_info('sockname')[1]
        holder.close()
        await asyncio.sleep(0)
        try:
            assert await async_setup_component(hass,'persistent_notification',{})
            flow = await hass.config_entries.flow.async_init("irtrans", context={"source":"user"})
            assert flow["type"] == "form", flow
            flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {
                "host":"127.0.0.1", "port":device_port, "receive_port":receive_port,
            })
            assert flow["type"] == "create_entry", flow
            entry = flow["result"]
            await hass.async_block_till_done()
            assert entry.state.value == "loaded", entry.state
            print('Config flow and integration setup:',entry.state)
            remote=next(state.entity_id for state in hass.states.async_all('remote'))
            sensor=next(state.entity_id for state in hass.states.async_all('sensor'))
            print('Entities:',remote,sensor)
            assert hass.states.get(remote).state=='on'
            controller=hass.data['irtrans']['entries'][entry.entry_id]
            events=[]
            receive_log_events=[]
            hass.bus.async_listen('irtrans_receive_log',lambda event:receive_log_events.append(event.data))
            sent_events=[]
            hass.bus.async_listen('irtrans_sent',lambda event:sent_events.append(event))
            hass.bus.async_listen('irtrans_event',lambda event: events.append(event.data))
            await hass.services.async_call('remote','learn_command',
                {'entity_id':remote,'device':'tv','command':['power'],'timeout':5},blocking=True)
            await hass.async_block_till_done()
            assert hass.states.get(remote).attributes['commands']=={'tv':['power']}
            storage=Path(config_dir)/'.storage'/f'irtrans_{entry.entry_id}_codes'
            assert storage.exists()
            stored=json.loads(storage.read_text())
            assert stored['data']['codes']['tv']['power']['hex']==fake.reply.packet.hex()
            print('Learn and durable HA Store save: OK')
            await hass.services.async_call('remote','send_command',
                {'entity_id':remote,'device':'tv','command':['power']},blocking=True)
            assert len(fake.sent)==1
            assert fake.sent[0][6:]==fake.reply.packet[6:]
            print('Send and real binary TCP acknowledgement: OK')
            transport.sendto(bytes((230,))+code(command=1).packet,('127.0.0.1',receive_port))
            await asyncio.sleep(.05)
            await hass.async_block_till_done()
            assert len(events)==1,events
            assert events[0]['remote']=='tv' and events[0]['button']=='power'
            assert events[0]['entity_id']==sensor
            assert hass.states.get(sensor).state=='tv/power'
            print('Receive, match, event and sensor: OK')
            # Duplicate command names in sequences are valid.
            await hass.services.async_call('remote','send_command',
                {'entity_id':remote,'device':'tv','command':['power','power'],
                 'num_repeats':2,'delay_secs':0},blocking=True)
            assert len(fake.sent)==5
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            controller=hass.data['irtrans']['entries'][entry.entry_id]
            assert controller.library=={'tv':['power']}
            print('Integration reload restores saved library: OK')
            await hass.services.async_call('remote','delete_command',
                {'entity_id':remote,'device':'tv','command':['power']},blocking=True)
            assert controller.library=={}
            # Native per-position naming and buttons work without YAML actions.
            texts=hass.states.async_all('text')
            buttons=hass.states.async_all('button')
            assert len(texts)==8 and len(buttons)==16,(len(texts),len(buttons))
            name=next(state.entity_id for state in texts if state.attributes.get('slot')==2)
            learn=next(state.entity_id for state in buttons if state.attributes.get('slot')==2 and state.attributes.get('action')=='learn')
            send=entity_registry.async_get(hass).async_get_entity_id('button','irtrans',f'{entry.entry_id}-slot-2-send')
            assert send is not None
            assert hass.states.get(send).state=='unavailable'
            await hass.services.async_call('text','set_value',{'entity_id':name,'value':'TV mute'},blocking=True)
            await hass.async_block_till_done()
            assert hass.states.get(name).state=='TV mute'
            assert 'TV mute' in hass.states.get(learn).attributes['friendly_name']
            await hass.services.async_call('button','press',{'entity_id':learn},blocking=True)
            await hass.async_block_till_done()
            assert hass.states.get(send).state!='unavailable'
            before=len(fake.sent)
            send_context=Context()
            await hass.services.async_call('button','press',{'entity_id':send},blocking=True,context=send_context)
            await hass.async_block_till_done()
            assert len(fake.sent)==before+1
            assert controller.last_sent['label']=='TV mute'
            assert sent_events[-1].context.id==send_context.id
            assert hass.states.get(controller.sent_sensor_entity_id).state=='TV mute'
            events.clear()
            transport.sendto(bytes((230,))+code(command=1).packet,('127.0.0.1',receive_port))
            await asyncio.sleep(.05)
            await hass.async_block_till_done()
            assert controller.last_received['label']=='TV mute'
            assert hass.states.get(sensor).state=='TV mute'
            assert events[-1]['label']=='TV mute'
            options=await hass.config_entries.options.async_init(entry.entry_id)
            assert options['type']=='menu',options
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'select_remote'})
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'remote':'main'})
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'name':'Living room','button_count':12})
            assert options['type']=='create_entry',options
            await hass.async_block_till_done()
            controller=hass.data['irtrans']['entries'][entry.entry_id]
            assert len(hass.states.async_all('text'))==12
            assert len(hass.states.async_all('button'))==24
            assert controller.slots['2']['label']=='TV mute'
            assert controller.slot_learned(2)
            assert hass.states.get(name).state=='TV mute'
            from homeassistant.helpers import device_registry
            registry=entity_registry.async_get(hass)
            devices=device_registry.async_get(hass)
            original_device=devices.async_get(registry.async_get(name).device_id)
            assert original_device.name=='Living room',original_device
            assert original_device.via_device_id is not None
            # A second remote gets independent slots, even with identical button names.
            options=await hass.config_entries.options.async_init(entry.entry_id)
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'add_remote'})
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'name':'Amplifier','button_count':4})
            assert options['type']=='create_entry',options
            await hass.async_block_till_done()
            controller=hass.data['irtrans']['entries'][entry.entry_id]
            assert len(hass.states.async_all('text'))==16
            assert len(hass.states.async_all('button'))==32
            new_name=registry.async_get_entity_id('text','irtrans',f'{entry.entry_id}-slot-1001-name')
            new_device=devices.async_get(registry.async_get(new_name).device_id)
            assert new_device.name=='Amplifier'
            assert new_device.id!=original_device.id
            assert controller.slots['1001']['device']!=controller.slots['1']['device']
            await hass.services.async_call('text','set_value',{'entity_id':new_name,'value':'Amp power'},blocking=True)
            new_learn=registry.async_get_entity_id('button','irtrans',f'{entry.entry_id}-slot-1001-learn')
            await hass.services.async_call('button','press',{'entity_id':new_learn},blocking=True)
            await hass.async_block_till_done()
            assert controller.slot_learned(1001)
            assert controller.slot_learned(2)
            # Hide and restore learned positions without losing bindings or IDs.
            for count in (1,12):
                options=await hass.config_entries.options.async_init(entry.entry_id)
                options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'select_remote'})
                options=await hass.config_entries.options.async_configure(options['flow_id'],{'remote':'main'})
                options=await hass.config_entries.options.async_configure(options['flow_id'],{'name':'Living room','button_count':count})
                await hass.async_block_till_done()
                controller=hass.data['irtrans']['entries'][entry.entry_id]
                assert controller.slots['2']['label']=='TV mute'
                assert controller.slot_learned(2)
                assert controller.slot_learned(1001)
            assert registry.async_get_entity_id('text','irtrans',f'{entry.entry_id}-slot-2-name')==name
            assert hass.states.get(name).state=='TV mute'
            # Output settings must preserve both remote definitions.
            options=await hass.config_entries.options.async_init(entry.entry_id)
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'output'})
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'led':'both'})
            await hass.async_block_till_done()
            controller=hass.data['irtrans']['entries'][entry.entry_id]
            assert len(controller.groups)==2
            assert entry.options['led']=='both'
            print('Multiple remote devices, rename/resize, independent code bindings and stable IDs: OK')
            from custom_components.irtrans.logbook import async_describe_events
            descriptions={}
            async_describe_events(hass,lambda domain,event,callback:descriptions.update({event:callback}))
            from types import SimpleNamespace
            described=descriptions['irtrans_receive_log'](SimpleNamespace(data=receive_log_events[-1]))
            assert described['message']=='received TV mute'
            automation_events_before=len(events)
            transport.sendto(bytes((230,))+code(data=b'11111',command=1).packet,('127.0.0.1',receive_port))
            await asyncio.sleep(.05)
            await hass.async_block_till_done()
            state=hass.states.get(controller.receive_log_entity_id)
            assert 'unknown command' in state.state,state
            assert '11111' in state.state
            assert state.attributes['last_packet']['kind']=='unknown'
            assert state.attributes['last_packet']['raw_hex']
            assert len(events)==automation_events_before
            transport.sendto(b'\xe6\x03\0',('127.0.0.1',receive_port))
            await asyncio.sleep(.05)
            await hass.async_block_till_done()
            state=hass.states.get(controller.receive_log_entity_id)
            assert state.attributes['last_packet']['kind']=='invalid'
            assert len(state.attributes['entries'])==2
            from custom_components.irtrans.diagnostics import async_get_config_entry_diagnostics
            diagnostic=await async_get_config_entry_diagnostics(hass,entry)
            assert len(diagnostic['receive_log'])==2
            assert 'codes' not in diagnostic
            print('Live unknown/invalid receive log, Activity descriptions and diagnostics: OK')
            print('Native naming, learning/sending buttons, labels, activity and configurable count: OK')
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert hass.data['irtrans']['hub'] is None
            assert not fake.errors,fake.errors
            print('Delete, unload and socket cleanup: OK')
            # Existing v1.41 entries have no unique_id or receive_port key.
            old = ConfigEntry(domain='irtrans',version=1,minor_version=1,source='user',
                title='IRTrans legacy entry',unique_id=None,
                data={'host':'127.0.0.1','port':str(device_port)},options={},
                discovery_keys=MappingProxyType({}),subentries_data=[])
            await hass.config_entries.async_add(old)
            await hass.async_block_till_done()
            assert old.state.value == 'loaded', old.state
            assert await hass.config_entries.async_unload(old.entry_id)
            print('Legacy host/port config entry: OK')
        finally:
            transport.close()
            await hass.async_stop()
            await fake.close()


asyncio.run(main())
