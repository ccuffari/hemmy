# Plugin auto-generato per il tool 'airflow.manage'.
# Installato via meta.install_tool con approvazione umana.

import os, json, urllib.request, urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _tok_arm():
    # Usa il choke-point condiviso (sessione OAuth dell'utente corrente,
    # fallback DefaultAzureCredential solo per CLI locale): PRIMA questa
    # funzione bypassava `get_arm_token` con una propria DefaultAzureCredential
    # diretta, condivisa tra tutti gli utenti del processo.
    try:
        return get_arm_token()
    except Exception:
        return None


def _req(method, url, token, body=None, auth_header='Bearer'):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Authorization', f'{auth_header} {token}')
    r.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            txt = x.read().decode()
            return {'ok': True, 'status': x.status, 'data': json.loads(txt) if txt else {}}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'status': e.code, 'error': e.read().decode()[:500]}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _af_creds():
    """Credenziali Airflow dell'utente corrente (thread-local), salvate via
    il bottone "Connetti Airflow" (`orchestrator_credentials.py`, cifrate a
    riposo in `user_secrets`). Fallback a env var SOLO per CLI locale senza
    portale (nessun utente legato al thread), mai in un processo web
    multi-utente — dove userebbe la STESSA connessione per tutti."""
    from hemmy.auth.user_context import get_current_user_id, get_users_store

    uid = get_current_user_id()
    if uid is not None:
        try:
            from hemmy.auth.orchestrator_credentials import load_airflow_credentials

            creds = load_airflow_credentials(get_users_store(), uid)
        except Exception:
            creds = None
        if creds:
            base = creds["base_url"].rstrip("/")
            if creds.get("auth_mode") == "token":
                auth = creds.get("token", "")
                header = "Bearer"
            else:
                import base64
                raw = f"{creds.get('username', '')}:{creds.get('password', '')}".encode("utf-8")
                auth = base64.b64encode(raw).decode("ascii")
                header = "Basic"
            return base, auth, header
    base = os.environ.get('AIRFLOW_BASE_URL')
    auth = os.environ.get('AIRFLOW_AUTH')
    return base, auth, 'Basic'


def run(**kwargs):
    action = kwargs.get('action')
    p = kwargs.get('params') or {}
    if action == 'list_managed_airflow':
        sub = get_subscription_id()
        if not sub:
            return {'ok': False, 'error': 'subscription non risolvibile'}
        token = _tok_arm()
        if not token:
            return {'ok': False, 'error': 'impossibile ottenere token Azure'}
        rg = p.get('resource_group')
        base = f'https://management.azure.com/subscriptions/{sub}' + (f'/resourceGroups/{rg}' if rg else '')
        r = _req('GET', f'{base}/providers/Microsoft.DataFactory/factories?api-version=2018-06-01', token)
        if not r.get('ok'):
            return r
        out = []
        for f in r['data'].get('value', []):
            frg = f['id'].split('/')[4]
            fn = f.get('name')
            rr = _req('GET', f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{frg}/providers/Microsoft.DataFactory/factories/{fn}/integrationRuntimes?api-version=2018-06-01', token)
            if rr.get('ok'):
                for ir in rr['data'].get('value', []):
                    if ir.get('properties', {}).get('type') == 'Airflow':
                        out.append({'factory': fn, 'resource_group': frg, 'ir_name': ir.get('name'), 'state': ir.get('properties', {}).get('state'), 'type': 'Airflow'})
        return {'ok': True, 'action': action, 'count': len(out), 'managed_airflow': out}
    base, auth, hdr = _af_creds()
    if not base:
        return {'ok': False, 'error': "connessione Airflow non configurata (credenziale 'airflow_base_url' nelle Impostazioni)"}
    if not auth:
        return {'ok': False, 'error': "connessione Airflow non configurata (credenziale 'airflow_password'/'airflow_token' nelle Impostazioni)"}
    api = base.rstrip('/') + '/api/v1'
    if action == 'list_dags':
        limit = p.get('limit', 100)
        r = _req('GET', f'{api}/dags?limit={limit}', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        out = [{'dag_id': x.get('dag_id'), 'is_paused': x.get('is_paused'), 'is_active': x.get('is_active'), 'schedule_interval': x.get('schedule_interval'), 'owners': x.get('owners'), 'tags': x.get('tags'), 'last_parsed_time': x.get('last_parsed_time')} for x in r['data'].get('dags', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'dags': out}
    if action == 'get_dag':
        did = p.get('dag_id')
        if not did:
            return {'ok': False, 'error': 'dag_id richiesto'}
        r = _req('GET', f'{api}/dags/{did}', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'dag': r['data']}
    if action == 'list_dag_runs':
        did = p.get('dag_id')
        limit = p.get('limit', 25)
        url = f'{api}/dags/{did}/dagRuns?limit={limit}' if did else f'{api}/dags/~/dagRuns?limit={limit}'
        r = _req('GET', url, auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        out = [{'dag_id': x.get('dag_id'), 'dag_run_id': x.get('dag_run_id'), 'state': x.get('state'), 'execution_date': x.get('execution_date'), 'start_date': x.get('start_date'), 'end_date': x.get('end_date'), 'run_type': x.get('run_type')} for x in r['data'].get('dag_runs', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'dag_runs': out}
    if action == 'get_dag_run':
        did = p.get('dag_id')
        rid = p.get('dag_run_id')
        if not did or not rid:
            return {'ok': False, 'error': 'dag_id e dag_run_id richiesti'}
        r = _req('GET', f'{api}/dags/{did}/dagRuns/{rid}', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'dag_run': r['data']}
    if action == 'trigger_dag':
        did = p.get('dag_id')
        if not did:
            return {'ok': False, 'error': 'dag_id richiesto'}
        body = {'conf': p.get('conf', {})}
        if p.get('logical_date'):
            body['logical_date'] = p['logical_date']
        r = _req('POST', f'{api}/dags/{did}/dagRuns', auth, body, auth_header=hdr)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'dag_id': did, 'dag_run': r['data']}
    if action == 'list_task_instances':
        did = p.get('dag_id')
        rid = p.get('dag_run_id')
        if not did or not rid:
            return {'ok': False, 'error': 'dag_id e dag_run_id richiesti'}
        r = _req('GET', f'{api}/dags/{did}/dagRuns/{rid}/taskInstances', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        out = [{'task_id': x.get('task_id'), 'state': x.get('state'), 'start_date': x.get('start_date'), 'end_date': x.get('end_date'), 'duration': x.get('duration'), 'try_number': x.get('try_number')} for x in r['data'].get('task_instances', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'task_instances': out}
    if action == 'list_variables':
        r = _req('GET', f'{api}/variables', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        out = [{'key': x.get('key'), 'description': x.get('description')} for x in r['data'].get('variables', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'variables': out}
    if action == 'list_connections':
        r = _req('GET', f'{api}/connections', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        out = [{'connection_id': x.get('connection_id'), 'conn_type': x.get('conn_type'), 'host': x.get('host'), 'schema': x.get('schema'), 'description': x.get('description')} for x in r['data'].get('connections', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'connections': out}
    if action == 'list_pools':
        r = _req('GET', f'{api}/pools', auth, auth_header=hdr)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'slots': x.get('slots'), 'occupied_slots': x.get('occupied_slots'), 'running_slots': x.get('running_slots'), 'queued_slots': x.get('queued_slots')} for x in r['data'].get('pools', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'pools': out}
    if action == 'get_airflow_summary':
        out = {}
        r = _req('GET', f'{api}/dags?limit=200', auth, auth_header=hdr)
        dags = r['data'].get('dags', []) if r.get('ok') else []
        out['dags_total'] = len(dags)
        out['dags_paused'] = sum(1 for x in dags if x.get('is_paused'))
        out['dags_active'] = sum(1 for x in dags if x.get('is_active'))
        r = _req('GET', f'{api}/dags/~/dagRuns?limit=50', auth, auth_header=hdr)
        runs = r['data'].get('dag_runs', []) if r.get('ok') else []
        out['runs_recent'] = len(runs)
        out['runs_failed'] = sum(1 for x in runs if x.get('state') == 'failed')
        out['runs_success'] = sum(1 for x in runs if x.get('state') == 'success')
        out['runs_running'] = sum(1 for x in runs if x.get('state') == 'running')
        return {'ok': True, 'action': action, 'summary': out}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "airflow.manage", "doc": "[WRITE] Gestione Apache Airflow: DAG, dag run, task instance, variabili, connessioni, pool. Supporta sia Azure Data Factory Managed Airflow (via ARM) sia Airflow self-hosted/Cloud Composer (via REST API). Connessione REST configurata come credenziale per-utente nelle Impostazioni ('Connetti Airflow': base_url + basic o token); autenticazione ereditata per Managed Airflow. Args: {\"action\": str, \"params\": {}}. Azioni: list_dags, get_dag, list_dag_runs, get_dag_run, trigger_dag, list_task_instances, list_variables, list_connections, list_pools, get_airflow_summary, list_managed_airflow.", "write": True, "entrypoint": "run"}
    ]
}
