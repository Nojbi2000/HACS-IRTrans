"""A grouped remote panel using existing HA entities and service permissions."""
import asyncio
from pathlib import Path
import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers import entity_registry
from .const import DOMAIN
from .groups import append_remote

URL = '/irtrans_static/panel-2.5.0.js'


async def async_register_panel(hass):
    state = hass.data[DOMAIN]
    if not state.get('panel_static_registered'):
        await hass.http.async_register_static_paths([
            StaticPathConfig(URL, str(Path(__file__).parent / 'frontend' / 'panel.js'), True)
        ])
        websocket_api.async_register_command(hass, websocket_panel)
        websocket_api.async_register_command(hass, websocket_add_remote)
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
    if state.get('panel_keep_visible', 0):
        return
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
            remotes.append({'key': f'{entry_id}/{group["id"]}', 'entry_id': entry_id, 'name': group['name'],
                            'device': controller.entry.title, 'slots': slots,
                            'hold_secs': controller.entry.options.get('hold_secs', 1),
                            'status_entity': registry.async_get_entity_id('sensor', DOMAIN,
                                f'{entry_id}-remote-{group["id"]}-learning-status')})
    connection.send_result(msg['id'], {'remotes': remotes})


@websocket_api.websocket_command({
    vol.Required('type'): 'irtrans/add_remote',
    vol.Required('entry_id'): str,
    vol.Required('name'): str,
    vol.Required('button_count'): int,
    vol.Required('request_id'): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_add_remote(hass, connection, msg):
    state = hass.data.get(DOMAIN, {})
    lock = state.setdefault('panel_write_lock', asyncio.Lock())
    async with lock:
        entry = hass.config_entries.async_get_entry(msg['entry_id'])
        controller = state.get('entries', {}).get(msg['entry_id'])
        if entry is None or entry.domain != DOMAIN or controller is None:
            connection.send_error(msg['id'], 'not_ready', 'IRTrans is not ready. Refresh the panel and try again.')
            return
        if controller.learning or controller._lock.locked():
            connection.send_error(msg['id'], 'busy', 'Wait for learning or transmission to finish before adding a remote.')
            return
        try:
            groups, group = append_remote(entry.options, msg['name'], msg['button_count'], msg['request_id'])
        except ValueError as exc:
            connection.send_error(msg['id'], 'invalid_input', str(exc))
            return
        key = f'{entry.entry_id}/{group["id"]}'
        if any(item['id'] == group['id'] for item in controller.groups):
            connection.send_result(msg['id'], {'key': key, 'ready': True})
            return
        # Keep the active panel mounted while the normal Options listener
        # reloads entities. Registration/static routes remain idempotent.
        state['panel_keep_visible'] = state.get('panel_keep_visible', 0) + 1
        try:
            hass.config_entries.async_update_entry(entry, options={**entry.options, 'remotes': groups})
            ready = False
            try:
                async with asyncio.timeout(20):
                    while True:
                        current = state.get('entries', {}).get(entry.entry_id)
                        if (current is not None and current is not controller
                                and any(item['id'] == group['id'] for item in current.groups)):
                            registry = entity_registry.async_get(hass)
                            entity_id = registry.async_get_entity_id('text', DOMAIN,
                                f'{entry.entry_id}-slot-{group["base"] + 1}-name')
                            if entity_id and hass.states.get(entity_id) is not None:
                                ready = True
                                break
                        if entry.state.value in ('setup_retry', 'setup_error', 'failed_unload'):
                            break
                        await asyncio.sleep(.1)
            except TimeoutError:
                pass
            connection.send_result(msg['id'], {'key': key, 'ready': ready})
        finally:
            state['panel_keep_visible'] -= 1
