# Plugin auto-generato per il tool 'databricks.manage'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse


def _api(workspace_url, token, method, path, body=None):
    url = workspace_url.rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return {'status': resp.status, 'body': json.loads(raw) if raw else {}}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return {'status': e.code, 'error': parsed}


def _user_secret(name):
    # Segreto per-utente (Impostazioni), cifrato in `user_secrets`. Fallback a
    # env var SOLO per CLI locale senza portale (nessun utente legato al thread);
    # mai in un processo web multi-utente, dove userebbe lo STESSO workspace/token
    # per tutti gli utenti collegati.
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _resolve_workspace_url(kwargs):
    url = kwargs.get('workspace_url') or _user_secret('databricks_host')
    if not url:
        raise ValueError(
            "workspace_url mancante: passalo in input oppure configura la credenziale "
            "'databricks_host' nelle Impostazioni (es. https://adb-<id>.<n>.azuredatabricks.net)"
        )
    if not url.startswith('http'):
        url = 'https://' + url
    return url


def run(**kwargs):
    action = kwargs.get('action')
    params = kwargs.get('params') or {}
    token = _user_secret('databricks_token')
    if not token:
        return {'ok': False, 'error': "Credenziale 'databricks_token' non configurata nelle Impostazioni. Imposta il token (PAT o SP) prima di usare il tool."}
    if not action:
        return {'ok': False, 'error': 'action mancante'}
    try:
        workspace_url = _resolve_workspace_url(kwargs)
    except ValueError as e:
        return {'ok': False, 'error': str(e)}

    # --- Cluster ---
    if action == 'list_clusters':
        r = _api(workspace_url, token, 'GET', '/api/2.0/clusters/list')
    elif action == 'get_cluster':
        r = _api(workspace_url, token, 'GET', '/api/2.0/clusters/get?cluster_id=' + str(params.get('cluster_id', '')))
    elif action == 'create_cluster':
        # Costruisce il body passando TUTTI i parametri forniti (cosi' supporta
        # data_security_mode, autoscale, custom_tags, spark_conf, ecc.).
        body = dict(params)
        body.setdefault('cluster_name', 'cluster')
        body.setdefault('spark_version', '13.3.x-scala2.12')
        body.setdefault('node_type_id', 'Standard_DS3_v2')
        if 'autoscale' not in body and 'num_workers' not in body:
            body['num_workers'] = 1
        r = _api(workspace_url, token, 'POST', '/api/2.0/clusters/create', body)
    elif action == 'start_cluster':
        r = _api(workspace_url, token, 'POST', '/api/2.0/clusters/start', {'cluster_id': params.get('cluster_id')})
    elif action == 'delete_cluster':
        r = _api(workspace_url, token, 'POST', '/api/2.0/clusters/delete', {'cluster_id': params.get('cluster_id')})

    # --- Job / Pipeline ---
    elif action == 'list_jobs':
        r = _api(workspace_url, token, 'GET', '/api/2.1/jobs/list')
    elif action == 'create_job':
        r = _api(workspace_url, token, 'POST', '/api/2.1/jobs/create', params.get('job', params))
    elif action == 'run_job':
        r = _api(workspace_url, token, 'POST', '/api/2.1/jobs/run-now', {'job_id': params.get('job_id')})
    elif action == 'get_job_run':
        r = _api(workspace_url, token, 'GET', '/api/2.1/jobs/runs/get?run_id=' + str(params.get('run_id', '')))
    elif action == 'list_job_runs':
        r = _api(workspace_url, token, 'GET', '/api/2.1/jobs/runs/list?job_id=' + str(params.get('job_id', '')))

    # --- Notebook / Workspace ---
    elif action == 'list_workspace_objects':
        r = _api(workspace_url, token, 'GET', '/api/2.0/workspace/list?path=' + urllib.parse.quote(params.get('path', '/')))
    elif action == 'mkdirs':
        r = _api(workspace_url, token, 'POST', '/api/2.0/workspace/mkdirs', {'path': params.get('path')})
    elif action == 'upload_notebook':
        import base64
        content = base64.b64encode(params.get('content', '').encode()).decode()
        r = _api(workspace_url, token, 'POST', '/api/2.0/workspace/import', {
            'path': params.get('path'),
            'format': params.get('format', 'SOURCE'),
            'language': params.get('language', 'PYTHON'),
            'content': content,
            'overwrite': params.get('overwrite', True),
        })
    elif action == 'get_workspace_status':
        r = _api(workspace_url, token, 'GET', '/api/2.0/workspace/get-status?path=' + urllib.parse.quote(params.get('path', '/')))

    # --- Secret scope ---
    elif action == 'list_secret_scopes':
        r = _api(workspace_url, token, 'GET', '/api/2.0/secrets/scopes/list')
    elif action == 'create_secret_scope':
        r = _api(workspace_url, token, 'POST', '/api/2.0/secrets/scopes/create', {'scope': params.get('scope')})
    elif action == 'put_secret':
        r = _api(workspace_url, token, 'POST', '/api/2.0/secrets/put', {
            'scope': params.get('scope'),
            'key': params.get('key'),
            'string_value': params.get('string_value'),
        })

    else:
        return {'ok': False, 'error': 'azione non supportata: ' + str(action)}

    if 'error' in r:
        return {'ok': False, 'action': action, 'status': r['status'], 'error': r['error']}
    return {'ok': True, 'action': action, 'status': r['status'], 'result': r['body']}

MANIFEST = {
    "tools": [
        {"name": "databricks.manage", "doc": "[WRITE] Gestisce il workspace Databricks (control-plane + workspace API) via REST API. Copre il ciclo di vita: configurazione iniziale, cluster, notebook, job/pipeline, secret scope. Host e token Databricks sono credenziali per-utente configurate nelle Impostazioni ('databricks_host'/'databricks_token'), mai esposte in chat. Args: {\"action\": str, \"workspace_url\": str (opz), \"params\": {} (opz)}. Azioni: list_clusters, create_cluster, get_cluster, delete_cluster, start_cluster, list_jobs, create_job, run_job, get_job_run, list_notebooks, upload_notebook, list_secret_scopes, create_secret_scope, put_secret, list_workspace_objects, mkdirs, get_workspace_status, list_job_runs.", "write": True, "entrypoint": "run"}
    ]
}
