# Plugin auto-generato per il tool 'logicapps.manage'.
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
    wf = p.get('workflow_name')
    if not rg:
        return {'ok': False, 'error': 'resource_group richiesto'}
    base = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Logic/workflows'
    if action == 'list_workflows':
        r = _req('GET', f'{base}?api-version=2019-05-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'state': pr.get('state'), 'location': x.get('location'), 'created_time': pr.get('createdTime'), 'changed_time': pr.get('changedTime'), 'provisioning_state': pr.get('provisioningState')})
        return {'ok': True, 'action': action, 'count': len(out), 'workflows': out}
    if action == 'get_workflow':
        if not wf:
            return {'ok': False, 'error': 'workflow_name richiesto'}
        r = _req('GET', f'{base}/{wf}?api-version=2019-05-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'name': wf, 'state': pr.get('state'), 'definition': pr.get('definition'), 'parameters': pr.get('parameters')}
    if action == 'list_runs':
        if not wf:
            return {'ok': False, 'error': 'workflow_name richiesto'}
        top = p.get('top', 25)
        r = _req('GET', f'{base}/{wf}/runs?api-version=2019-05-01&$top={top}', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'status': pr.get('status'), 'start_time': pr.get('startTime'), 'end_time': pr.get('endTime'), 'trigger': pr.get('trigger', {}).get('name'), 'error': pr.get('error')})
        return {'ok': True, 'action': action, 'count': len(out), 'runs': out}
    if action == 'get_run':
        if not wf:
            return {'ok': False, 'error': 'workflow_name richiesto'}
        run_id = p.get('run_id')
        if not run_id:
            return {'ok': False, 'error': 'run_id richiesto'}
        r = _req('GET', f'{base}/{wf}/runs/{run_id}?api-version=2019-05-01', token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'run': r['data']}
    if action == 'list_triggers':
        if not wf:
            return {'ok': False, 'error': 'workflow_name richiesto'}
        r = _req('GET', f'{base}/{wf}/triggers?api-version=2019-05-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'type': pr.get('type'), 'state': pr.get('state'), 'recurrence': pr.get('recurrence'), 'last_run': pr.get('lastRun')})
        return {'ok': True, 'action': action, 'count': len(out), 'triggers': out}
    if action == 'get_trigger_history':
        if not wf:
            return {'ok': False, 'error': 'workflow_name richiesto'}
        trg = p.get('trigger_name')
        if not trg:
            return {'ok': False, 'error': 'trigger_name richiesto'}
        r = _req('GET', f'{base}/{wf}/triggers/{trg}/histories?api-version=2019-05-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'status': pr.get('status'), 'start_time': pr.get('startTime'), 'end_time': pr.get('endTime'), 'error': pr.get('error')})
        return {'ok': True, 'action': action, 'count': len(out), 'history': out}
    if action == 'get_workflow_summary':
        r = _req('GET', f'{base}?api-version=2019-05-01', token)
        if not r.get('ok'):
            return r
        wfs = r['data'].get('value', [])
        enabled = sum(1 for x in wfs if x.get('properties', {}).get('state') == 'Enabled')
        return {'ok': True, 'action': action, 'total': len(wfs), 'enabled': enabled, 'disabled': len(wfs) - enabled}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "logicapps.manage", "doc": "[WRITE] Gestione Logic Apps: lista workflow, dettagli, run history, trigger, azioni, stato. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_workflows, get_workflow, list_runs, get_run, list_triggers, get_trigger_history, get_workflow_summary.", "write": True, "entrypoint": "run"}
    ]
}
