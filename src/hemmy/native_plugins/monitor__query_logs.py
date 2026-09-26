# Plugin auto-generato per il tool 'monitor.query_logs'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def _resolve_workspace(workspace_name, resource_group):
    if workspace_name and len(workspace_name) == 36 and workspace_name.count('-') == 4:
        return workspace_name
    sub = get_subscription_id()
    if not sub:
        return None
    token = _get_token('https://management.azure.com/.default')
    if not token:
        return None
    if resource_group:
        url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
               f'/providers/Microsoft.OperationalInsights/workspaces?api-version=2022-10-01')
    else:
        url = (f'https://management.azure.com/subscriptions/{sub}'
               f'/providers/Microsoft.OperationalInsights/workspaces?api-version=2022-10-01')
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    for ws in data.get('value', []):
        if ws.get('name') == workspace_name:
            return (ws.get('properties') or {}).get('customerId')
    return None


def run(**kwargs):
    query = kwargs.get('query')
    if not query:
        return {'ok': False, 'error': 'parametro query (KQL) obbligatorio'}
    workspace_name = kwargs.get('workspace_name') or os.environ.get('LOG_ANALYTICS_WORKSPACE')
    resource_group = kwargs.get('resource_group') or os.environ.get('LOG_ANALYTICS_RG')
    timespan = kwargs.get('timespan', 'PT1H')
    max_rows = int(kwargs.get('max_rows', 100))

    if not workspace_name:
        return {'ok': False, 'error': 'workspace_name non fornito e LOG_ANALYTICS_WORKSPACE non impostato'}

    workspace_id = _resolve_workspace(workspace_name, resource_group)
    if not workspace_id:
        return {'ok': False, 'error': f'workspace {workspace_name} non trovato (verifica nome/RG o permessi ARM)'}

    token = _get_token('https://api.loganalytics.io/.default')
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Log Analytics'}

    url = f'https://api.loganalytics.io/v1/workspaces/{workspace_id}/query'
    body = json.dumps({'query': query, 'timespan': timespan}).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    tables = data.get('tables') or []
    if not tables:
        return {'ok': True, 'rows': [], 'count': 0, 'workspace_id': workspace_id}
    t = tables[0]
    cols = [c.get('name') for c in t.get('columns', [])]
    rows = [dict(zip(cols, r)) for r in t.get('rows', [])[:max_rows]]
    return {'ok': True, 'columns': cols, 'rows': rows, 'count': len(rows), 'workspace_id': workspace_id, 'timespan': timespan}

MANIFEST = {
    "tools": [
        {"name": "monitor.query_logs", "doc": "[READ] Esegue una query KQL su un Log Analytics workspace e restituisce i risultati. Risolve il workspace via ARM REST (nome o GUID). Args: {\"query\": str (KQL), \"workspace_name\": str (opz, default da env LOG_ANALYTICS_WORKSPACE), \"resource_group\": str (opz, default da env LOG_ANALYTICS_RG), \"timespan\": str (opz, default 'PT1H'), \"max_rows\": int (opz, default 100)}. Ritorna le righe e il conteggio.", "write": False, "entrypoint": "run"}
    ]
}
