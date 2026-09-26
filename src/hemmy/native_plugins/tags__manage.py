# Plugin auto-generato per il tool 'tags.manage'.
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


def _sub():
    return get_subscription_id()
def _list_resources(rg, token, sub):
    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/resources?api-version=2021-04-01'
           if rg else f'https://management.azure.com/subscriptions/{sub}/resources?api-version=2021-04-01')
    out = []
    while url:
        r = _req('GET', url, token)
        if not r.get('ok'):
            break
        d = r['data']
        out += d.get('value', [])
        url = d.get('nextLink')
    return out


def _tags_url(rid):
    return f'https://management.azure.com{rid}/providers/Microsoft.Resources/tags/default?api-version=2021-04-01'


def run(**kwargs):
    action = kwargs.get('action')
    p = kwargs.get('params') or {}
    sub = _sub()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _tok()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}
    rg = p.get('resource_group')
    rid = p.get('resource_id')
    if action == 'list_resource_tags':
        res = _list_resources(rg, token, sub)
        out = [{'name': r.get('name'), 'type': r.get('type'), 'rg': r['id'].split('/')[4], 'id': r.get('id'), 'tags': r.get('tags') or {}} for r in res]
        return {'ok': True, 'action': action, 'count': len(out), 'resources': out}
    if action == 'list_rg_tags':
        url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}?api-version=2021-04-01'
               if rg else f'https://management.azure.com/subscriptions/{sub}/resourcegroups?api-version=2021-04-01')
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        if rg:
            return {'ok': True, 'action': action, 'rg': rg, 'tags': r['data'].get('tags') or {}}
        out = [{'rg': x.get('name'), 'tags': x.get('tags') or {}} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'resource_groups': out}
    if action == 'get_resource_tags':
        if not rid:
            return {'ok': False, 'error': 'resource_id richiesto'}
        r = _req('GET', _tags_url(rid), token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'resource_id': rid, 'tags': r['data'].get('properties', {}).get('tags') or {}}
    if action == 'set_resource_tags':
        if not rid:
            return {'ok': False, 'error': 'resource_id richiesto'}
        tags = p.get('tags') or {}
        r = _req('PATCH', _tags_url(rid), token, {'operation': 'Merge', 'properties': {'tags': tags}})
        return {'ok': r.get('ok'), 'action': action, 'resource_id': rid, 'tags': tags, 'status': r.get('status'), 'error': r.get('error')}
    if action == 'delete_resource_tags':
        if not rid:
            return {'ok': False, 'error': 'resource_id richiesto'}
        keys = p.get('tag_keys') or []
        r = _req('GET', _tags_url(rid), token)
        if not r.get('ok'):
            return r
        cur = r['data'].get('properties', {}).get('tags') or {}
        for k in keys:
            cur.pop(k, None)
        r2 = _req('PATCH', _tags_url(rid), token, {'operation': 'Replace', 'properties': {'tags': cur}})
        return {'ok': r2.get('ok'), 'action': action, 'resource_id': rid, 'removed': keys, 'status': r2.get('status'), 'error': r2.get('error')}
    if action == 'set_rg_tags':
        if not rg:
            return {'ok': False, 'error': 'resource_group richiesto'}
        tags = p.get('tags') or {}
        url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Resources/tags/default?api-version=2021-04-01'
        r = _req('PATCH', url, token, {'operation': 'Merge', 'properties': {'tags': tags}})
        return {'ok': r.get('ok'), 'action': action, 'rg': rg, 'tags': tags, 'status': r.get('status'), 'error': r.get('error')}
    if action == 'list_all_tags':
        url = f'https://management.azure.com/subscriptions/{sub}/providers/Microsoft.Resources/tags/default?api-version=2021-04-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'tags': r['data'].get('properties', {}).get('tags') or {}}
    if action == 'cost_by_tag':
        tag = p.get('tag')
        if not tag:
            return {'ok': False, 'error': 'tag richiesto'}
        url = f'https://management.azure.com/subscriptions/{sub}/providers/Microsoft.CostManagement/query?api-version=2023-11-01'
        body = {'type': 'ActualCost', 'timeframe': 'MonthToDate', 'dataset': {'granularity': 'None', 'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}}, 'grouping': [{'type': 'Tag', 'name': tag}]}}
        r = _req('POST', url, token, body)
        if not r.get('ok'):
            return r
        props = r['data'].get('properties', {})
        cols = [c.get('name') for c in props.get('columns', [])]
        rows = props.get('rows', [])
        out = [dict(zip(cols, row)) for row in rows]
        return {'ok': True, 'action': action, 'tag': tag, 'rows': out}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "tags.manage", "doc": "[WRITE] Gestione tag Azure per FinOps/chargeback: lista tag su risorse/RG, applica/rimuovi tag, costi aggregati per tag. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_resource_tags, list_rg_tags, get_resource_tags, set_resource_tags, delete_resource_tags, set_rg_tags, list_all_tags, cost_by_tag.", "write": True, "entrypoint": "run"}
    ]
}
