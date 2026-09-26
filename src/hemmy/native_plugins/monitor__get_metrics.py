# Plugin auto-generato per il tool 'monitor.get_metrics'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse


def _get_token():
    try:
        from hemmy.utils.azure_auth import get_arm_token
        return get_arm_token()
    except Exception:
        return None


def run(**kwargs):
    resource_id = kwargs.get('resource_id')
    metric_names = kwargs.get('metric_names')
    if not resource_id or not metric_names:
        return {'ok': False, 'error': 'resource_id e metric_names sono obbligatori'}
    if isinstance(metric_names, str):
        metric_names = [metric_names]
    timespan = kwargs.get('timespan', 'PT1H')
    interval = kwargs.get('interval', 'PT5M')
    aggregation = kwargs.get('aggregation', 'Average')

    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    params = urllib.parse.urlencode({
        'api-version': '2018-01-01',
        'metricnames': ','.join(metric_names),
        'timespan': timespan,
        'interval': interval,
        'aggregation': aggregation,
    })
    url = f'https://management.azure.com{resource_id}/providers/microsoft.insights/metrics?{params}'
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    series = []
    for m in data.get('value', []):
        name = (m.get('name') or {}).get('value')
        unit = m.get('unit')
        points = []
        for ts in m.get('timeseries', []):
            for dp in ts.get('data', []):
                points.append({'time': dp.get('timeStamp'), aggregation.lower(): dp.get(aggregation)})
        series.append({'metric': name, 'unit': unit, 'points': points})
    return {'ok': True, 'count': len(series), 'series': series, 'timespan': timespan, 'interval': interval}

MANIFEST = {
    "tools": [
        {"name": "monitor.get_metrics", "doc": "[READ] Legge le metriche Azure di una risorsa (CPU, DTU, throughput, ecc.) via ARM Metrics API. Args: {\"resource_id\": str, \"metric_names\": [str], \"timespan\": str (opz, es. 'PT1H' o '2024-01-01T00:00:00Z/2024-01-01T01:00:00Z'), \"interval\": str (opz, default 'PT5M'), \"aggregation\": str (opz, default 'Average')}. Ritorna serie temporali per metrica.", "write": False, "entrypoint": "run"}
    ]
}
