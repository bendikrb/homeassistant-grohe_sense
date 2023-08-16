import html
import logging
import asyncio
import collections
import re
from urllib.parse import urlparse

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.helpers import aiohttp_client
from aiohttp import ClientSession, hdrs

from .const import (
    DOMAIN,
    BASE_URL,
    CONF_REFRESH_TOKEN,
    LOGIN_URL,
    REFRESH_URL,
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema({
            vol.Required(CONF_USERNAME): cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
            vol.Optional(CONF_REFRESH_TOKEN): cv.string,
        }),
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)

GroheDevice = collections.namedtuple(
    'GroheDevice', [
        'location_id',
        'room_id',
        'appliance_id',
        'type',
        'name',
    ])


async def async_setup(hass, config):
    _LOGGER.debug("Loading Grohe Sense")

    grohe_config = config.get(DOMAIN)
    auth_session, devices = await initialize_shared_objects(hass, grohe_config.get(CONF_USERNAME), grohe_config.get(CONF_PASSWORD))

    hass.data[DOMAIN] = {'session': auth_session, 'devices': devices}

    await hass.helpers.discovery.async_load_platform('sensor', DOMAIN, {}, config)
    await hass.helpers.discovery.async_load_platform('switch', DOMAIN, {}, config)
    return True


async def initialize_shared_objects(hass, username, password):
    session = aiohttp_client.async_get_clientsession(hass)
    auth_session = OauthSession(session, username, password)
    devices = []

    locations = await auth_session.get(BASE_URL + f'locations')
    for location in locations:
        _LOGGER.debug('Found location %s', location)
        location_id = location['id']
        rooms = await auth_session.get(BASE_URL + f'locations/{location_id}/rooms')
        for room in rooms:
            _LOGGER.debug('Found room %s', room)
            room_id = room['id']
            if appliances := await auth_session.get(BASE_URL + f'locations/{location_id}/rooms/{room_id}/appliances'):
                for appliance in appliances:
                    _LOGGER.debug('Found appliance %s', appliance)
                    appliance_id = appliance['appliance_id']
                    devices.append(GroheDevice(location_id, room_id, appliance_id, appliance['type'], appliance['name']))

    return auth_session, devices


class OauthException(Exception):
    def __init__(self, error_code, reason):
        self.error_code = error_code
        self.reason = reason


class OauthSession:
    def __init__(self, session: ClientSession, username, password):
        self._session = session
        self._refresh_token = None
        self._username = username
        self._password = password
        self._access_token = None
        self._fetching_new_token = None

    @property
    def session(self):
        return self._session

    async def login(self):
        _LOGGER.debug("Attempting login ...")
        redirect = await self._session.get(LOGIN_URL, allow_redirects=False)
        redirect.raise_for_status()

        login_url = redirect.headers['Location']
        _LOGGER.debug("Got redirect: %s", login_url)
        login_form = await self._session.get(login_url)
        login_form.raise_for_status()

        regex = r'action="([^ >"]*)"'
        matches = re.findall(regex, await login_form.text('utf-8'), re.MULTILINE)
        login_url = html.unescape(matches[0])

        _LOGGER.debug("Got login_url from login_form: %s", login_url)

        response = await self._session.post(
            login_url,
            allow_redirects=False,
            data={
                "username": self._username,
                "password": self._password,
            })
        response.raise_for_status()

        r_url = urlparse(response.headers.get(hdrs.LOCATION))
        token_url = r_url._replace(scheme="https").geturl()

        _LOGGER.debug("Login successful, token URL: %s", token_url)

        token_result = await self._session.get(token_url)
        token_result.raise_for_status()
        token = await token_result.json()

        _LOGGER.debug("Got token: %s", token)

        return token

    async def token(self, old_token=None):
        """ Returns an authorization header. If one is supplied as old_token, invalidate that one """
        if self._access_token not in (None, old_token):
            return self._access_token

        if self._fetching_new_token is not None:
            await self._fetching_new_token.wait()
            return self._access_token

        self._access_token = None
        self._fetching_new_token = asyncio.Event()

        headers = {'Content-Type': 'application/json'}

        if self._refresh_token:
            data = {'refresh_token': self._refresh_token}
            refresh_response = await self._http_request(BASE_URL + 'oidc/refresh', 'post', headers=headers, json=data)
            if 'access_token' in refresh_response:
                self._access_token = 'Bearer ' + refresh_response['access_token']
            else:
                _LOGGER.error('OAuth token refresh did not yield access token! Got back %s', refresh_response)
        else:
            token = await self.login()
            self._refresh_token = token['refresh_token']
            self._access_token = 'Bearer ' + token['access_token']

        self._fetching_new_token.set()
        self._fetching_new_token = None
        return self._access_token

    async def get(self, url, **kwargs):
        return await self._http_request(url, auth_token=self, **kwargs)

    async def post(self, url, json, **kwargs):
        return await self._http_request(url, method='post', auth_token=self, json=json, **kwargs)

    async def _http_request(self, url, method='get', auth_token=None, headers=None, **kwargs):
        if headers is None:
            headers = {}
        _LOGGER.debug('Making http %s request to %s, headers %s', method, url, headers)
        headers = headers.copy()
        tries = 0
        while True:
            if auth_token is not None:
                # Cache token so we know which token was used for this request,
                # so we know if we need to invalidate.
                token = await auth_token.token()
                headers['Authorization'] = token
            try:
                async with self._session.request(method, url, headers=headers, **kwargs) as response:
                    _LOGGER.debug("Http %s request to %s got response %d", method, url, response.status)
                    if response.status in (200, 201):
                        return await response.json()
                    elif response.status == 401:
                        if auth_token is not None:
                            _LOGGER.debug("Request to %s returned status %d, refreshing auth token", url,
                                          response.status)
                            token = await auth_token.token(token)
                        else:
                            _LOGGER.error(
                                "Grohe sense refresh token is invalid (or expired), please update your configuration "
                                "with a new refresh token"
                            )
                            raise OauthException(response.status, await response.text())
                    else:
                        _LOGGER.debug("Request to %s returned status %d, %s", url, response.status,
                                      await response.json())
                        return None
            except OauthException as oe:
                raise
            except Exception as e:  # pylint: disable=broad-exception-caught
                _LOGGER.debug('Exception for http %s request to %s: %s', method, url, e)

            tries += 1
            await asyncio.sleep(min(600, 2 ** tries))


class GroheSenseUtil:
    _appliance_id = None
    _room_id = None
    _location_id = None

    def get_url(self, endpoint):
        return "{base}{paths}{endpoint}".format(
            base=BASE_URL,
            paths=f"locations/{self._location_id}/rooms/{self._room_id}/appliances/{self._appliance_id}/",
            endpoint=endpoint,
        )
