# Plugin auto-generato per il tool 'fabric.manage'.
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
    base = f'https://management.azure.com/subscriptions/{sub}' + (f'/resourceGroups/{rg}' if rg else '')
    if action == 'list_capacities':
        r = _req('GET', f'{base}/providers/Microsoft.Fabric/capacities?api-version=2023-11-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'location': x.get('location'), 'sku': x.get('sku', {}).get('name'), 'state': pr.get('state'), 'provisioning_state': pr.get('provisioningState'), 'admin_members': pr.get('administration', {}).get('members')})
        return {'ok': True, 'action': action, 'count': len(out), 'capacities': out}
    if action == 'get_capacity':
        cap = p.get('capacity_name')
        if not rg or not cap:
            return {'ok': False, 'error': 'resource_group e capacity_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Fabric/capacities/{cap}?api-version=2023-11-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'name': cap, 'state': pr.get('state'), 'sku': r['data'].get('sku', {}).get('name'), 'provisioning_state': pr.get('provisioningState'), 'administration': pr.get('administration')}
    if action == 'list_workspaces':
        r = _req('GET', 'https://api.fabric.microsoft.com/v1/workspaces', token)
        if not r.get('ok'):
            return {'ok': False, 'error': 'Fabric API richiede token Fabric (non ARM). Usa list_capacities per la parte ARM.', 'detail': r.get('error')}
        out = [{'id': x.get('id'), 'name': x.get('displayName'), 'type': x.get('type'), 'description': x.get('description')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'workspaces': out}
    if action == 'get_workspace':
        wid = p.get('workspace_id')
        if not wid:
            return {'ok': False, 'error': 'workspace_id richiesto'}
        r = _req('GET', f'https://api.fabric.microsoft.com/v1/workspaces/{wid}', token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'workspace': r['data']}
    if action == 'list_items':
        wid = p.get('workspace_id')
        if not wid:
            return {'ok': False, 'error': 'workspace_id richiesto'}
        r = _req('GET', f'https://api.fabric.microsoft.com/v1/workspaces/{wid}/items', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('displayName'), 'type': x.get('type'), 'description': x.get('description')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'items': out}
    if action == 'list_lakehouses':
        wid = p.get('workspace_id')
        if not wid:
            return {'ok': False, 'error': 'workspace_id richiesto'}
        r = _req('GET', f'https://api.fabric.microsoft.com/v1/workspaces/{wid}/lakehouses', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('displayName'), 'description': x.get('description')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'lakehouses': out}
    if action == 'list_warehouses':
        wid = p.get('workspace_id')
        if not wid:
            return {'ok': False, 'error': 'workspace_id richiesto'}
        r = _req('GET', f'https://api.fabric.microsoft.com/v1/workspaces/{wid}/warehouses', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('displayName'), 'description': x.get('description')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'warehouses': out}
    if action == 'get_fabric_summary':
        r = _req('GET', f'{base}/providers/Microsoft.Fabric/capacities?api-version=2023-11-01', token)
        caps = r['data'].get('value', []) if r.get('ok') else []
        active = sum(1 for x in caps if x.get('properties', {}).get('state') == 'Active')
        return {'ok': True, 'action': action, 'capacity_count': len(caps), 'active_capacities': active, 'capacities': [{'name': x.get('name'), 'rg': x['id'].split('/')[4], 'sku': x.get('sku', {}).get('name'), 'state': x.get('properties', {}).get('state')} for x in caps]}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "fabric.manage", "doc": "[WRITE] Gestione Microsoft Fabric: workspace, capacità, lakehouse, warehouse, notebook, pipeline, semantic model. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_capacities, get_capacity, list_workspaces, get_workspace, list_items, list_lakehouses, list_warehouses, get_fabric_summary.", "write": True, "entrypoint": "run"}
    ]
}
