"""Real HA 2026.11.1 integration test with fake UDP hardware, no external network.

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
from test_binary import FakeDevice, code

from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.core import HomeAssistant
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
        fake=FakeDevice()
        transport,_=await asyncio.get_running_loop().create_datagram_endpoint(
            lambda:fake, local_addr=('127.0.0.1',0))
        device_port=transport.get_extra_info('sockname')[1]
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
            print('Send and real UDP acknowledgement: OK')
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


asyncio.run(main())
