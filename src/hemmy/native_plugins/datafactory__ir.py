# Plugin auto-generato per il tool 'datafactory.ir'.
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


def _base(sub, rg, factory):
    return f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.DataFactory/factories/{factory}'


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
    factory = p.get('factory_name')
    ir = p.get('ir_name')
    if not rg or not factory:
        return {'ok': False, 'error': 'resource_group e factory_name richiesti'}
    b = _base(sub, rg, factory)
    if action == 'list_irs':
        r = _req('GET', f'{b}/integrationRuntimes?api-version=2018-06-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'type': pr.get('type'), 'state': pr.get('state'), 'location': x.get('location')})
        return {'ok': True, 'action': action, 'count': len(out), 'integration_runtimes': out}
    if action == 'get_ir':
        if not ir:
            return {'ok': False, 'error': 'ir_name richiesto'}
        r = _req('GET', f'{b}/integrationRuntimes/{ir}?api-version=2018-06-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'name': ir, 'type': pr.get('type'), 'state': pr.get('state'), 'description': pr.get('description'), 'properties': pr}
    if action == 'get_ir_status':
        if not ir:
            return {'ok': False, 'error': 'ir_name richiesto'}
        r = _req('POST', f'{b}/integrationRuntimes/{ir}/getStatus?api-version=2018-06-01', token, {})
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'name': ir, 'status': r['data']}
    if action == 'list_ir_nodes':
        if not ir:
            return {'ok': False, 'error': 'ir_name richiesto'}
        r = _req('GET', f'{b}/integrationRuntimes/{ir}/nodes?api-version=2018-06-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'node': x.get('name'), 'status': pr.get('status'), 'version': pr.get('version'), 'capabilities': pr.get('capabilities'), 'last_connect_time': pr.get('lastConnectTime'), 'last_end_update_time': pr.get('lastEndUpdateTime'), 'last_start_update_time': pr.get('lastStartUpdateTime'), 'last_stop_time': pr.get('lastStopTime'), 'node_name': pr.get('nodeName'), 'cpu_utilization': pr.get('cpuUtilization'), 'memory_utilization': pr.get('memoryUtilization'), 'concurrent_jobs_limit': pr.get('concurrentJobsLimit'), 'available_memory': pr.get('availableMemory'), 'running_job_count': pr.get('runningJobCount')})
        return {'ok': True, 'action': action, 'name': ir, 'count': len(out), 'nodes': out}
    if action == 'start_ir':
        if not ir:
            return {'ok': False, 'error': 'ir_name richiesto'}
        r = _req('POST', f'{b}/integrationRuntimes/{ir}/start?api-version=2018-06-01', token, {})
        return {'ok': r.get('ok'), 'action': action, 'name': ir, 'status': r.get('status'), 'error': r.get('error')}
    if action == 'stop_ir':
        if not ir:
            return {'ok': False, 'error': 'ir_name richiesto'}
        r = _req('POST', f'{b}/integrationRuntimes/{ir}/stop?api-version=2018-06-01', token, {})
        return {'ok': r.get('ok'), 'action': action, 'name': ir, 'status': r.get('status'), 'error': r.get('error')}
    if action == 'get_ir_metrics':
        if not ir:
            return {'ok': False, 'error': 'ir_name richiesto'}
        rid = f'/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.DataFactory/factories/{factory}/integrationRuntimes/{ir}'
        url = f'https://management.azure.com{rid}/providers/microsoft.insights/metrics?api-version=2018-01-01&metricnames=IntegrationRuntimeAvailableMemory,IntegrationRuntimeCpuPercentage,IntegrationRuntimeAvailableNodeNumber&timespan=PT1H&interval=PT5M&aggregation=Average,Maximum'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = {}
        for m in r['data'].get('value', []):
            n = m.get('name', {}).get('value')
            ser = m.get('timeseries', [{}])[0].get('data', [])
            for a in ('average', 'maximum'):
                vals = [x.get(a) for x in ser if x.get(a) is not None]
                if vals:
                    out[f'{n}_{a}'] = round(max(vals) if a == 'maximum' else vals[-1], 2)
        return {'ok': True, 'action': action, 'name': ir, 'metrics': out}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "datafactory.ir", "doc": "[WRITE] Gestione Integration Runtime di Azure Data Factory: lista IR, dettagli, stato nodi, heartbeat, start/stop, restart nodo. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_irs, get_ir, get_ir_status, list_ir_nodes, start_ir, stop_ir, get_ir_metrics.", "write": True, "entrypoint": "run"}
    ]
}
