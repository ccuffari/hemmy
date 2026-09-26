# Plugin auto-generato per il tool 'dbt.manage'.
# Installato via meta.install_tool con approvazione umana.

import os, json, urllib.request, urllib.error


def _user_secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _tok():
    return _user_secret('dbt_api_token')


def _acc():
    return _user_secret('dbt_account_id')


def _req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Authorization', 'Token ' + token)
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
    token = _tok()
    if not token:
        return {'ok': False, 'error': "Credenziale 'dbt_api_token' non configurata nelle Impostazioni"}
    acc = p.get('account_id') or _acc()
    base = 'https://cloud.getdbt.com/api/v2'
    if action == 'list_accounts':
        r = _req('GET', f'{base}/accounts/', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('name'), 'plan': x.get('plan'), 'state': x.get('state')} for x in r['data'].get('data', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'accounts': out}
    if not acc:
        return {'ok': False, 'error': "account_id richiesto (param o credenziale 'dbt_account_id' nelle Impostazioni)"}
    if action == 'list_projects':
        r = _req('GET', f'{base}/accounts/{acc}/projects/', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('name'), 'repository': x.get('repository', {}).get('remote_url'), 'state': x.get('state')} for x in r['data'].get('data', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'projects': out}
    if action == 'list_jobs':
        r = _req('GET', f'{base}/accounts/{acc}/jobs/', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('name'), 'project_id': x.get('project_id'), 'environment_id': x.get('environment_id'), 'state': x.get('state'), 'schedule': x.get('schedule', {}).get('cron'), 'last_run': x.get('last_run', {}).get('status') if x.get('last_run') else None} for x in r['data'].get('data', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'jobs': out}
    if action == 'get_job':
        jid = p.get('job_id')
        if not jid:
            return {'ok': False, 'error': 'job_id richiesto'}
        r = _req('GET', f'{base}/accounts/{acc}/jobs/{jid}/', token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'job': r['data'].get('data')}
    if action == 'list_runs':
        jid = p.get('job_id')
        limit = p.get('limit', 25)
        url = f'{base}/accounts/{acc}/runs/?limit={limit}' + (f'&job_definition_id={jid}' if jid else '')
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'job_id': x.get('job_definition_id'), 'status': x.get('status_humanized'), 'started_at': x.get('started_at'), 'finished_at': x.get('finished_at'), 'duration': x.get('duration'), 'is_success': x.get('is_success')} for x in r['data'].get('data', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'runs': out}
    if action == 'get_run':
        rid = p.get('run_id')
        if not rid:
            return {'ok': False, 'error': 'run_id richiesto'}
        r = _req('GET', f'{base}/accounts/{acc}/runs/{rid}/', token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'run': r['data'].get('data')}
    if action == 'trigger_job':
        jid = p.get('job_id')
        if not jid:
            return {'ok': False, 'error': 'job_id richiesto'}
        body = {'cause': p.get('cause', 'Triggered via API')}
        if p.get('steps'):
            body['steps_override'] = p['steps']
        r = _req('POST', f'{base}/accounts/{acc}/jobs/{jid}/run/', token, body)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'job_id': jid, 'run': r['data'].get('data')}
    if action == 'list_environments':
        r = _req('GET', f'{base}/accounts/{acc}/environments/', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'name': x.get('name'), 'project_id': x.get('project_id'), 'type': x.get('type'), 'dbt_version': x.get('dbt_version'), 'state': x.get('state')} for x in r['data'].get('data', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'environments': out}
    if action == 'get_run_artifacts':
        rid = p.get('run_id')
        if not rid:
            return {'ok': False, 'error': 'run_id richiesto'}
        r = _req('GET', f'{base}/accounts/{acc}/runs/{rid}/artifacts/', token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'artifacts': r['data'].get('data', [])}
    if action == 'get_dbt_summary':
        out = {}
        r = _req('GET', f'{base}/accounts/{acc}/jobs/', token)
        jobs = r['data'].get('data', []) if r.get('ok') else []
        out['jobs_total'] = len(jobs)
        out['jobs_active'] = sum(1 for x in jobs if x.get('state') == 1)
        r = _req('GET', f'{base}/accounts/{acc}/runs/?limit=50', token)
        runs = r['data'].get('data', []) if r.get('ok') else []
        out['runs_recent'] = len(runs)
        out['runs_failed'] = sum(1 for x in runs if x.get('is_success') is False)
        out['runs_success'] = sum(1 for x in runs if x.get('is_success') is True)
        return {'ok': True, 'action': action, 'summary': out}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "dbt.manage", "doc": "[WRITE] Gestione dbt Cloud: account, progetti, job, run, environment, modelli, test. Usa dbt Cloud API v2 (token e account sono credenziali per-utente configurate nelle Impostazioni: 'dbt_api_token', 'dbt_account_id'). Args: {\"action\": str, \"params\": {}}. Azioni: list_accounts, list_projects, list_jobs, get_job, list_runs, get_run, trigger_job, list_environments, get_run_artifacts, get_dbt_summary.", "write": True, "entrypoint": "run"}
    ]
}
