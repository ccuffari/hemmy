# Plugin auto-generato per il tool 'storage.get_lifecycle_policy'.
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
    if not resource_group or not account_name:
        return {'ok': False, 'error': 'resource_group e account_name obbligatori'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
           f'/providers/Microsoft.Storage/storageAccounts/{account_name}'
           f'/managementPolicies/default?api-version=2023-01-01')
    try:
        data = _get(url, token)
        props = data.get('properties', {})
        rules = []
        for r in props.get('policy', {}).get('rules', []):
            rules.append({
                'name': r.get('name'),
                'enabled': r.get('enabled'),
                'type': r.get('type'),
                'definition': r.get('definition'),
            })
        return {'ok': True, 'account': account_name, 'count': len(rules), 'rules': rules}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'ok': True, 'account': account_name, 'count': 0, 'rules': [],
                    'note': 'nessuna lifecycle policy configurata'}
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "storage.get_lifecycle_policy", "doc": "[READ] Legge la lifecycle management policy di uno storage account (regole tiering/expiration). Args: {\"resource_group\": str, \"account_name\": str}. Ritorna regole e filtri.", "write": False, "entrypoint": "run"}
    ]
}
