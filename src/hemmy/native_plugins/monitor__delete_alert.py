# Plugin auto-generato per il tool 'monitor.delete_alert'.
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
    if not name or not resource_group:
        return {'ok': False, 'error': 'parametri name e resource_group obbligatori'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
           f'/providers/Microsoft.Insights/metricAlerts/{name}?api-version=2018-03-01')
    req = urllib.request.Request(url, method='DELETE')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        return {'ok': True, 'name': name, 'deleted': True}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'ok': True, 'name': name, 'deleted': False, 'note': 'alert non trovato (gia eliminato)'}
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "monitor.delete_alert", "doc": "[WRITE] Elimina una Metric Alert Azure. Args: {\"name\": str, \"resource_group\": str}. Ritorna l'esito dell'eliminazione.", "write": True, "entrypoint": "run"}
    ]
}
