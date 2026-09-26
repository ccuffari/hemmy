# Plugin auto-generato per il tool 'purview.manage'.
# Installato via meta.install_tool con approvazione umana.

import os, json, urllib.request, urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _tok():
    # Choke-point condiviso: sessione OAuth dell'utente corrente prima
    # di tutto, DefaultAzureCredential solo per CLI locale (vedi
    # utils/azure_auth.get_arm_token). PRIMA questa funzione bypassava
    # get_arm_token con una propria DefaultAzureCredential diretta,
    # condivisa tra tutti gli utenti del processo.
    try:
        return get_arm_token()
    except Exception:
        return None


def _req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Authorization', 'Bearer ' + token)
    r.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            txt = x.read().decode()
            return {'ok': True, 'status': x.status, 'data': json.loads(txt) if txt else {}}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'status': e.code, 'error': e.read().decode()[:500]}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def run(**kwargs):
    action = kwargs.get('action')
    p = kwargs.get('params') or {}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _tok()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}
    rg = p.get('resource_group')
    acc = p.get('account_name')
    base = f'https://management.azure.com/subscriptions/{sub}' + (f'/resourceGroups/{rg}' if rg else '')
    if action == 'list_accounts':
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts?api-version=2023-05-01-preview', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'location': x.get('location'), 'provisioning_state': pr.get('provisioningState'), 'endpoint': pr.get('endpoints', {}).get('catalog'), 'public_network_access': pr.get('publicNetworkAccess'), 'managed_rg': pr.get('managedResourceGroupName')})
        return {'ok': True, 'action': action, 'count': len(out), 'accounts': out}
    if action == 'get_account':
        if not rg or not acc:
            return {'ok': False, 'error': 'resource_group e account_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts/{acc}?api-version=2023-05-01-preview', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'name': acc, 'provisioning_state': pr.get('provisioningState'), 'endpoints': pr.get('endpoints'), 'public_network_access': pr.get('publicNetworkAccess'), 'managed_rg': pr.get('managedResourceGroupName'), 'sku': r['data'].get('sku', {}).get('name')}
    if action == 'list_collections':
        if not rg or not acc:
            return {'ok': False, 'error': 'resource_group e account_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts/{acc}/collections?api-version=2019-11-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'friendly_name': x.get('properties', {}).get('friendlyName'), 'description': x.get('properties', {}).get('description'), 'parent': x.get('properties', {}).get('parentCollection', {}).get('referenceName')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'collections': out}
    if action == 'list_data_sources':
        if not rg or not acc:
            return {'ok': False, 'error': 'resource_group e account_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts/{acc}/dataSources?api-version=2023-05-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'kind': x.get('kind'), 'created_at': x.get('properties', {}).get('createdAt'), 'last_modified_at': x.get('properties', {}).get('lastModifiedAt')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'data_sources': out}
    if action == 'list_scans':
        if not rg or not acc:
            return {'ok': False, 'error': 'resource_group e account_name richiesti'}
        ds = p.get('data_source_name')
        if not ds:
            return {'ok': False, 'error': 'data_source_name richiesto'}
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts/{acc}/dataSources/{ds}/scans?api-version=2023-05-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'kind': x.get('kind'), 'scan_ruleset': x.get('properties', {}).get('scanRulesetName'), 'trigger': x.get('properties', {}).get('trigger')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'scans': out}
    if action == 'list_classifications':
        if not rg or not acc:
            return {'ok': False, 'error': 'resource_group e account_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts/{acc}/classificationRules?api-version=2023-05-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'kind': x.get('kind'), 'classification_name': x.get('properties', {}).get('classificationName'), 'description': x.get('properties', {}).get('description')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'classifications': out}
    if action == 'get_purview_summary':
        r = _req('GET', f'{base}/providers/Microsoft.Purview/accounts?api-version=2023-05-01-preview', token)
        if not r.get('ok'):
            return r
        accounts = r['data'].get('value', [])
        return {'ok': True, 'action': action, 'account_count': len(accounts), 'accounts': [{'name': x.get('name'), 'rg': x['id'].split('/')[4], 'location': x.get('location'), 'endpoint': x.get('properties', {}).get('endpoints', {}).get('catalog')} for x in accounts]}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "purview.manage", "doc": "[WRITE] Gestione Microsoft Purview (data governance): account, collezioni, asset catalogati, lineage, classificazioni, scansioni. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_accounts, get_account, list_collections, list_data_sources, list_scans, list_classifications, get_purview_summary.", "write": True, "entrypoint": "run"}
    ]
}
