"""A grouped remote panel using existing HA entities and service permissions."""
from pathlib import Path
import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers import entity_registry
from .const import DOMAIN

URL = '/irtrans_static/panel-2.4.1.js'


async def async_register_panel(hass):
    state = hass.data[DOMAIN]
    if not state.get('panel_static_registered'):
        await hass.http.async_register_static_paths([
            StaticPathConfig(URL, str(Path(__file__).parent / 'frontend' / 'panel.js'), True)
        ])
        websocket_api.async_register_command(hass, websocket_panel)
        state['panel_static_registered'] = True
    if not state.get('panel_registered'):
        await panel_custom.async_register_panel(
            hass, frontend_url_path='irtrans', webcomponent_name='irtrans-panel',
            sidebar_title='IRTrans', sidebar_icon='mdi:remote', module_url=URL,
            embed_iframe=False, require_admin=True,
        )
        state['panel_registered'] = True


def async_remove_panel(hass):
    state = hass.data[DOMAIN]
    if state.get('panel_registered'):
        frontend.async_remove_panel(hass, 'irtrans')
        state['panel_registered'] = False


@websocket_api.websocket_command({vol.Required('type'): 'irtrans/panel'})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_panel(hass, connection, msg):
    registry = entity_registry.async_get(hass)
    remotes = []
    for controller in hass.data.get(DOMAIN, {}).get('entries', {}).values():
        entry_id = controller.entry.entry_id
        for group in controller.groups:
            slots = []
            for number in range(1, group['button_count'] + 1):
                index = group['base'] + number
                entities = {action: registry.async_get_entity_id(
                    'text' if action == 'name' else 'button', DOMAIN,
                    f'{entry_id}-slot-{index}-{action}')
                    for action in ('name', 'learn', 'learn_repeat', 'send', 'hold')}
                slots.append({'index': index, 'number': number,
                              'label': controller.slots[str(index)]['label'],
                              'learned': controller.slot_learned(index),
                              'repeat_learned': controller.slot_repeat_learned(index),
                              'entities': entities})
            remotes.append({'key': f'{entry_id}/{group["id"]}', 'name': group['name'],
                            'device': controller.entry.title, 'slots': slots,
                            'hold_secs': controller.entry.options.get('hold_secs', 1),
                            'status_entity': registry.async_get_entity_id('sensor', DOMAIN,
                                f'{entry_id}-remote-{group["id"]}-learning-status')})
    connection.send_result(msg['id'], {'remotes': remotes})
