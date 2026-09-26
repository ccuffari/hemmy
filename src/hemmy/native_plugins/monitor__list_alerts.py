# Plugin auto-generato per il tool 'monitor.list_alerts'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def run(**kwargs):
    resource_group = kwargs.get('resource_group')
    only_firing = bool(kwargs.get('only_firing', False))
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    if resource_group:
        url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
               f'/providers/Microsoft.Insights/metricAlerts?api-version=2018-03-01')
    else:
        url = (f'https://management.azure.com/subscriptions/{sub}'
               f'/providers/Microsoft.Insights/metricAlerts?api-version=2018-03-01')

    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    alerts = []
    for a in data.get('value', []):
        props = a.get('properties', {})
        criteria = props.get('criteria', {}).get('allOf', [])
        c0 = criteria[0] if criteria else {}
        item = {
            'name': a.get('name'),
            'id': a.get('id'),
            'enabled': props.get('enabled'),
            'severity': props.get('severity'),
            'scopes': props.get('scopes'),
            'metric': c0.get('metricName'),
            'operator': c0.get('operator'),
            'threshold': c0.get('threshold'),
            'aggregation': c0.get('timeAggregation'),
            'description': props.get('description'),
        }
        alerts.append(item)

    return {'ok': True, 'count': len(alerts), 'alerts': alerts}

MANIFEST = {
    "tools": [
        {"name": "monitor.list_alerts", "doc": "[READ] Elenca le Metric Alert di un resource group (o subscription) con stato e configurazione. Args: {\"resource_group\": str (opz), \"only_firing\": bool (opz, default false)}. Ritorna nome, severità, stato, target, soglia.", "write": False, "entrypoint": "run"}
    ]
}
