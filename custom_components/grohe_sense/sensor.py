import logging
import asyncio
import collections
from datetime import (datetime, timezone, timedelta)

from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
from homeassistant.const import (
    DEVICE_CLASS_HUMIDITY,
    DEVICE_CLASS_PRESSURE,
    DEVICE_CLASS_TEMPERATURE,
    PERCENTAGE,
    PRESSURE_BAR,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
    VOLUME_FLOW_RATE_CUBIC_METERS_PER_HOUR,
    VOLUME_LITERS,
)

from . import GroheSenseUtil
from .const import (
    DOMAIN,
    GROHE_SENSE_TYPE,
    GROHE_SENSE_GUARD_TYPE,
    NOTIFICATION_TYPES,
)

_LOGGER = logging.getLogger(__name__)

SensorType = collections.namedtuple('SensorType', ['unit', 'device_class', 'function'])

SENSOR_TYPES = {
    'temperature': SensorType(TEMP_CELSIUS, DEVICE_CLASS_TEMPERATURE, lambda x: x),
    'humidity': SensorType(PERCENTAGE, DEVICE_CLASS_HUMIDITY, lambda x: x),
    'flowrate': SensorType(VOLUME_FLOW_RATE_CUBIC_METERS_PER_HOUR, None, lambda x: x * 3.6),
    'pressure': SensorType(PRESSURE_BAR, DEVICE_CLASS_PRESSURE, lambda x: x * 1000),
    'temperature_guard': SensorType(TEMP_CELSIUS, DEVICE_CLASS_TEMPERATURE, lambda x: x),
}

SENSOR_TYPES_PER_UNIT = {
    GROHE_SENSE_TYPE: ['temperature', 'humidity'],
    GROHE_SENSE_GUARD_TYPE: ['flowrate', 'pressure', 'temperature_guard']
}

NOTIFICATION_UPDATE_DELAY = timedelta(minutes=1)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    _LOGGER.debug("Starting Grohe Sense sensor")

    if DOMAIN not in hass.data or 'devices' not in hass.data[DOMAIN]:
        _LOGGER.error(
            "Did not find shared objects. You may need to update your configuration (this module should no longer be "
            "configured under sensor).")
        return

    auth_session = hass.data[DOMAIN]['session']

    entities = []
    for device in hass.data[DOMAIN]['devices']:
        reader = GroheSenseGuardReader(auth_session, device.location_id, device.room_id, device.appliance_id,
                                       device.type)
        entities.append(
            GroheSenseNotificationEntity(auth_session, device.location_id, device.room_id, device.appliance_id,
                                         device.name))
        if device.type in SENSOR_TYPES_PER_UNIT:
            entities += [GroheSenseSensorEntity(reader, device.name, key) for key in SENSOR_TYPES_PER_UNIT[device.type]]
            if device.type == GROHE_SENSE_GUARD_TYPE:  # The sense guard also gets sensor entities for water flow
                entities.append(GroheSenseGuardWithdrawalsEntity(reader, device.name, 1))
                entities.append(GroheSenseGuardWithdrawalsEntity(reader, device.name, 7))
        else:
            _LOGGER.warning('Unrecognized appliance %s, ignoring.', device)
    if entities:
        async_add_entities(entities)


class GroheSenseGuardReader(GroheSenseUtil):
    def __init__(self, auth_session, location_id, room_id, appliance_id, device_type):
        self._auth_session = auth_session
        self._location_id = location_id
        self._room_id = room_id
        self._appliance_id = appliance_id
        self._type = device_type

        self._withdrawals = []
        self._measurements = {}
        self._poll_from = datetime.now(tz=timezone.utc) - timedelta(7)
        self._fetching_data = None
        self._data_fetch_completed = datetime.min

    @property
    def appliance_id(self):
        """ returns the appliance Identifier, looks like a UUID, so hopefully unique """
        return self._appliance_id

    async def async_update(self):
        if self._fetching_data is not None:
            await self._fetching_data.wait()
            return

        # XXX: Hardcoded 15 minute interval for now. Would be prettier to set this a bit more dynamically
        # based on the json response for the sense guard, and probably hardcode something longer for the sense.
        if datetime.now() - self._data_fetch_completed < timedelta(minutes=15):
            _LOGGER.debug('Skipping fetching new data, time since last fetch was only %s',
                          datetime.now() - self._data_fetch_completed)
            return

        _LOGGER.debug("Fetching new data for appliance %s", self._appliance_id)
        self._fetching_data = asyncio.Event()

        def parse_time(s):
            # XXX: Fix for python 3.6 - Grohe emits time zone as "+HH:MM", python 3.6's %z only accepts the format +HHMM
            # So, some ugly code to remove the colon for now...
            if s.rfind(':') > s.find('+'):
                s = s[:s.rfind(':')] + s[s.rfind(':') + 1:]
            return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%f%z')

        poll_from = self._poll_from.strftime('%Y-%m-%d')
        measurements_response = await self._auth_session.get(self.get_url(f"data?from={poll_from}"))
        if 'withdrawals' in measurements_response['data']:
            withdrawals = measurements_response['data']['withdrawals']
            _LOGGER.debug('Received %d withdrawals in response', len(withdrawals))
            for w in withdrawals:
                w['starttime'] = parse_time(w['starttime'])
            withdrawals = [w for w in withdrawals if w['starttime'] > self._poll_from]
            withdrawals.sort(key=lambda x: x['starttime'])

            _LOGGER.debug('Got %d new withdrawals totaling %f volume', len(withdrawals),
                          sum((w['waterconsumption'] for w in withdrawals)))
            self._withdrawals += withdrawals
            if len(self._withdrawals) > 0:
                self._poll_from = max(self._poll_from, self._withdrawals[-1]['starttime'])
        elif self._type != GROHE_SENSE_TYPE:
            _LOGGER.info('Data response for appliance %s did not contain any withdrawals data', self._appliance_id)

        if 'measurement' in measurements_response['data']:
            measurements = measurements_response['data']['measurement']
            measurements.sort(key=lambda x: x['timestamp'])
            if len(measurements):
                for key in SENSOR_TYPES_PER_UNIT[self._type]:
                    if key in measurements[-1]:
                        self._measurements[key] = measurements[-1][key]
                self._poll_from = max(self._poll_from, parse_time(measurements[-1]['timestamp']))
        else:
            _LOGGER.info('Data response for appliance %s did not contain any measurements data', self._appliance_id)

        self._data_fetch_completed = datetime.now()

        self._fetching_data.set()
        self._fetching_data = None

    def consumption(self, since):
        # XXX: As self._withdrawals is sorted, we could speed this up by a binary search,
        #      but most likely data sets are small enough that a linear scan is fine.
        return sum((w['waterconsumption'] for w in self._withdrawals if w['starttime'] >= since))

    def measurement(self, key):
        if key in self._measurements:
            return self._measurements[key]
        return STATE_UNKNOWN


class GroheSenseNotificationEntity(Entity, GroheSenseUtil):
    def __init__(self, auth_session, location_id, room_id, appliance_id, name):
        self._auth_session = auth_session
        self._location_id = location_id
        self._room_id = room_id
        self._appliance_id = appliance_id
        self._name = name
        self._notifications = []

    @property
    def name(self):
        return f'{self._name} notifications'

    @property
    def state(self):
        def truncate_string(l, s):
            if len(s) > l:
                return s[:l - 4] + ' ...'
            return s

        return truncate_string(255, '\n'.join([
            NOTIFICATION_TYPES.get((n['category'], n['type']), 'Unknown notification: {}'.format(n))
            for n in self._notifications
        ]))

    @Throttle(NOTIFICATION_UPDATE_DELAY)
    async def async_update(self):
        self._notifications = await self._auth_session.get(self.get_url("notifications"))


class GroheSenseGuardWithdrawalsEntity(Entity):
    def __init__(self, reader, name, days):
        self._reader = reader
        self._name = name
        self._days = days

    # @property
    # def unique_id(self):
    #    return '{}-{}'.format(self._reader.appliance_id, self._days)

    @property
    def name(self):
        return '{} {} day'.format(self._name, self._days)

    @property
    def unit_of_measurement(self):
        return VOLUME_LITERS

    @property
    def state(self):
        if self._days == 1:  # special case, if we're averaging over 1 day, just count since midnight local time
            since = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # otherwise, it's a rolling X day average
            since = datetime.now(tz=timezone.utc) - timedelta(self._days)
        return self._reader.consumption(since)

    async def async_update(self):
        await self._reader.async_update()


class GroheSenseSensorEntity(Entity):
    def __init__(self, reader, name, key):
        self._reader = reader
        self._name = name
        self._key = key

    @property
    def name(self):
        return '{} {}'.format(self._name, self._key)

    @property
    def unit_of_measurement(self):
        return SENSOR_TYPES[self._key].unit

    @property
    def device_class(self):
        return SENSOR_TYPES[self._key].device_class

    @property
    def state(self):
        raw_state = self._reader.measurement(self._key)
        if raw_state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return raw_state
        else:
            return SENSOR_TYPES[self._key].function(raw_state)

    async def async_update(self):
        await self._reader.async_update()
