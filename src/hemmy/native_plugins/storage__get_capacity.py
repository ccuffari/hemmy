# Plugin auto-generato per il tool 'storage.get_capacity'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def _get(url, token):
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(**kwargs):
    resource_group = kwargs.get('resource_group')
    account_name = kwargs.get('account_name')
    timespan = kwargs.get('timespan', 'PT1H')
    if not resource_group or not account_name:
        return {'ok': False, 'error': 'resource_group e account_name obbligatori'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    res_id = (f'/subscriptions/{sub}/resourceGroups/{resource_group}'
              f'/providers/Microsoft.Storage/storageAccounts/{account_name}')
    metrics = 'UsedCapacity,Transactions,Ingress,Egress,Availability,SuccessE2ELatency'
    url = (f'https://management.azure.com{res_id}/providers/microsoft.insights/metrics'
           f'?api-version=2018-01-01&metricnames={metrics}&timespan={timespan}&interval=PT5M&aggregation=Average,Total')
    try:
        data = _get(url, token)
        out = []
        for m in data.get('value', []):
            series = []
            for ts in m.get('timeseries', []):
                for pt in ts.get('data', []):
                    series.append({'time': pt.get('timeStamp'),
                                   'avg': pt.get('average'), 'total': pt.get('total')})
            out.append({'metric': m.get('name', {}).get('value'), 'unit': m.get('unit'), 'series': series})
        return {'ok': True, 'account': account_name, 'timespan': timespan, 'metrics': out}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "storage.get_capacity", "doc": "[READ] Legge capacità e utilizzo di uno storage account via ARM Metrics API (used_capacity, transactions, ingress/egress, availability). Args: {\"resource_group\": str, \"account_name\": str, \"timespan\": str (opz, default 'PT1H')}. Ritorna metriche di capacità e traffico.", "write": False, "entrypoint": "run"}
    ]
}
