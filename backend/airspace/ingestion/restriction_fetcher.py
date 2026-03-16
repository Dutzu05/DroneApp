from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Any

RESTRICTION_ZONES_URL = 'https://flightplan.romatsa.ro/init/static/zone_restrictionate_uav.json'


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.set_ciphers('DEFAULT:!DH')
    return ctx


def fetch_restriction_zones() -> dict[str, Any]:
    req = urllib.request.Request(RESTRICTION_ZONES_URL, headers={'User-Agent': 'DroneApp-AirspaceIngestion/1.0'})
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=60) as resp:
        return json.loads(resp.read())
