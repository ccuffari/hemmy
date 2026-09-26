# Plugin auto-generato per il tool 'monitor.create_alert'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def run(**kwargs):
    name = kwargs.get('name')
    resource_group = kwargs.get('resource_group')
    target_resource_id = kwargs.get('target_resource_id')
    metric_name = kwargs.get('metric_name')
    operator = kwargs.get('operator', 'GreaterThan')
    threshold = kwargs.get('threshold')
    aggregation = kwargs.get('aggregation', 'Average')
    window_size = kwargs.get('window_size', 'PT5M')
    evaluation_frequency = kwargs.get('evaluation_frequency', 'PT1M')
    severity = int(kwargs.get('severity', 2))
    action_group_id = kwargs.get('action_group_id')
    description = kwargs.get('description', '')

    missing = [k for k, v in {'name': name, 'resource_group': resource_group, 'target_resource_id': target_resource_id, 'metric_name': metric_name, 'threshold': threshold}.items() if v is None]
    if missing:
        return {'ok': False, 'error': f'parametri obbligatori mancanti: {missing}'}

    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}

    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
           f'/providers/Microsoft.Insights/metricAlerts/{name}?api-version=2018-03-01')

    criteria = {
        'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria',
        'allOf': [{
            'name': 'c1',
            'metricName': metric_name,
            'metricNamespace': kwargs.get('metric_namespace', ''),
            'operator': operator,
            'threshold': float(threshold),
            'timeAggregation': aggregation,
            'criterionType': 'StaticThresholdCriterion',
        }],
    }
    actions = []
    if action_group_id:
        actions.append({'actionGroupId': action_group_id})

    body = {
        'location': 'global',
        'properties': {
            'description': description,
            'severity': severity,
            'enabled': True,
            'scopes': [target_resource_id],
            'evaluationFrequency': evaluation_frequency,
            'windowSize': window_size,
            'criteria': criteria,
            'actions': actions,
        },
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method='PUT')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
        return {'ok': True, 'id': result.get('id'), 'name': result.get('name'), 'created': True}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "monitor.create_alert", "doc": "[WRITE] Crea/aggiorna una Metric Alert Azure su una risorsa, con action group per le notifiche. Args: {\"name\": str, \"resource_group\": str, \"target_resource_id\": str, \"metric_name\": str, \"operator\": \"GreaterThan\"|\"LessThan\"|\"GreaterOrLessThan\", \"threshold\": float, \"aggregation\": \"Average\"|\"Total\"|\"Count\"|\"Max\"|\"Min\", \"window_size\": str (opz, default 'PT5M'), \"evaluation_frequency\": str (opz, default 'PT1M'), \"severity\": int (opz, 0-4, default 2), \"action_group_id\": str (opz), \"description\": str (opz)}. Crea l'alert via ARM.", "write": True, "entrypoint": "run"}
    ]
}
