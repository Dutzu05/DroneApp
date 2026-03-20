from __future__ import annotations

import json
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Any

GEOSERVER_BASE = 'https://flightplan.romatsa.ro/init/geoserver/ows'
WFS_PARAMS = (
    'service=WFS&version=1.0.0&request=GetFeature'
    '&maxFeatures=50000&outputFormat=application%2Fjson'
)


@dataclass(frozen=True)
class RomatsaLayer:
    source: str
    type_name: str
    srs_name: str | None = None


ROMATSA_LAYERS = {
    'romatsa_wfs_ctr': RomatsaLayer(source='romatsa_wfs_ctr', type_name='carto:CTR_LRBB', srs_name='EPSG:4326'),
    'romatsa_wfs_tma': RomatsaLayer(source='romatsa_wfs_tma', type_name='opr:tma_boundary', srs_name='EPSG:4326'),
    'notam_wfs': RomatsaLayer(source='notam_wfs', type_name='carto:restrictii_notam_pt_uav', srs_name='EPSG:4326'),
}


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.set_ciphers('DEFAULT:!DH')
    return ctx


def fetch_romatsa_layer(source: str) -> dict[str, Any]:
    layer = ROMATSA_LAYERS[source]
    url = f'{GEOSERVER_BASE}?{WFS_PARAMS}&typeName={layer.type_name}'
    if layer.srs_name:
        url += f'&srsName={layer.srs_name}'
    req = urllib.request.Request(url, headers={'User-Agent': 'DroneApp-AirspaceIngestion/1.0'})
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=60) as resp:
        return json.loads(resp.read())
