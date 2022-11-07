#!/usr/bin/env python
# -*- coding: utf-8 -*-
import requests
from lxml import html
import json


def get_token(uname, pwd):
    _cookie = None
    config = {
                "username": uname,
                "password": pwd
    }
    BASE_URL = 'https://idp2-apigw.cloud.grohe.com/'
    _token = None

    try:
        _session = requests.session()
        _r = _session.get(url=BASE_URL + 'v3/iot/oidc/login')
    except Exception as e:
        print(str(e))
    else:
        _cookie = _r.cookies
        tree = html.fromstring(_r.content)

        _name = tree.xpath("//html/body/div/div/div/div/div/div/div/form")
        _action = _name[0].action

        _payload = {'username': config['username'],
                    'password': config['password'],
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'origin': BASE_URL,
                    'referer': BASE_URL + 'v3/iot/oidc/login',
                    'X-Requested-With': 'XMLHttpRequest'}
        try:
            _r = _session.post(url=_action, data=_payload, cookies=_cookie, allow_redirects=False)
        except Exception as e:
            print(str(e))
        else:
            _ondus_url = _r.next.url.replace('ondus', 'https')
            try:
                _r = _session.get(url=_ondus_url, cookies=_cookie)
            except Exception as e:
                print(str(e))
            else:
                _json = json.loads(_r.text)

    return _json


if __name__ == '__main__':
    _token = get_token("username", "password")
    print("Refresh token: ", _token['refresh_token'])
    for k in _token:
        print(k, _token[k])