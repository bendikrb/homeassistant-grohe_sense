import logging
from homeassistant.components.switch import SwitchEntity

from homeassistant.util import Throttle
from homeassistant.const import STATE_UNKNOWN

from . import GroheSenseUtil
from .const import (
    DOMAIN,
    GROHE_SENSE_GUARD_TYPE,
    VALVE_UPDATE_DELAY,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    _LOGGER.debug("Starting Grohe Sense valve switch")
    auth_session = hass.data[DOMAIN]['session']
    entities = []

    for device in filter(lambda d: d.type == GROHE_SENSE_GUARD_TYPE, hass.data[DOMAIN]['devices']):
        entities.append(GroheSenseGuardValve(
            auth_session, device.location_id, device.room_id, device.appliance_id, device.name
        ))
    if entities:
        async_add_entities(entities)


class GroheSenseGuardValve(SwitchEntity, GroheSenseUtil):
    def __init__(self, auth_session, location_id, room_id, appliance_id, name):
        self._auth_session = auth_session
        self._location_id = location_id
        self._room_id = room_id
        self._appliance_id = appliance_id
        self._name = name
        self._is_on = STATE_UNKNOWN

    @property
    def name(self):
        return '{} valve'.format(self._name)

    @property
    def is_on(self):
        return self._is_on

    @property
    def icon(self):
        return 'mdi:water'

    @property
    def device_class(self):
        return 'switch'

    @Throttle(VALVE_UPDATE_DELAY)
    async def async_update(self):
        command_response = await self._auth_session.get(self.get_url("command"))
        if 'command' in command_response and 'valve_open' in command_response['command']:
            self._is_on = command_response['command']['valve_open']
        else:
            _LOGGER.error('Failed to parse out valve_open from commands response: %s', command_response)

    async def _set_state(self, state):
        data = {'type': GROHE_SENSE_GUARD_TYPE, 'command': {'valve_open': state}}
        command_response = await self._auth_session.post(self.get_url("command"), data)
        if 'command' in command_response and 'valve_open' in command_response['command']:
            self._is_on = command_response['command']['valve_open']
        else:
            _LOGGER.warning('Got unknown response back when setting valve state: %s', command_response)

    async def async_turn_on(self, **kwargs):
        _LOGGER.info('Turning on water for %s', self._name)
        await self._set_state(True)

    async def async_turn_off(self, **kwargs):
        _LOGGER.info('Turning off water for %s', self._name)
        await self._set_state(False)
