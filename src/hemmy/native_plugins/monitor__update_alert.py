# Plugin auto-generato per il tool 'monitor.update_alert'.
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


def _put(url, token, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method='PUT')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(**kwargs):
    name = kwargs.get('name')
    resource_group = kwargs.get('resource_group')
    if not name or not resource_group:
        return {'ok': False, 'error': 'name e resource_group obbligatori'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
           f'/providers/Microsoft.Insights/metricAlerts/{name}?api-version=2018-03-01')
    try:
        current = _get(url, token)
        props = current.get('properties', {})
        criteria = props.get('criteria', {})
        allof = criteria.get('allOf', [{}])
        if kwargs.get('threshold') is not None:
            allof[0]['threshold'] = float(kwargs['threshold'])
        if kwargs.get('window_size'):
            props['windowSize'] = kwargs['window_size']
        if kwargs.get('evaluation_frequency'):
            props['evaluationFrequency'] = kwargs['evaluation_frequency']
        if kwargs.get('severity') is not None:
            props['severity'] = int(kwargs['severity'])
        if kwargs.get('enabled') is not None:
            props['enabled'] = bool(kwargs['enabled'])
        if kwargs.get('action_group_id'):
            props['actions'] = [{'actionGroupId': kwargs['action_group_id']}]
        criteria['allOf'] = allof
        props['criteria'] = criteria
        body = {'location': current.get('location', 'global'), 'properties': props}
        result = _put(url, token, body)
        return {'ok': True, 'name': name, 'updated': True,
                'severity': result.get('properties', {}).get('severity'),
                'enabled': result.get('properties', {}).get('enabled')}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "monitor.update_alert", "doc": "[WRITE] Aggiorna una Metric Alert esistente (soglia, finestra, severità, action group). Args: {\"name\": str, \"resource_group\": str, \"threshold\": float (opz), \"window_size\": str (opz), \"evaluation_frequency\": str (opz), \"severity\": int (opz), \"action_group_id\": str (opz), \"enabled\": bool (opz)}. Ritorna l'alert aggiornato.", "write": True, "entrypoint": "run"}
    ]
}
