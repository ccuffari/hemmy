# Plugin auto-generato per il tool 'monitor.health_probe'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _token():
    try:
        return get_arm_token(), None
    except Exception as e:
        return None, {'stage': 'token', 'error': str(e)}
def _api(method, path, body=None, token=None):
    url = 'https://management.azure.com' + path
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
    except Exception as e:
        return {'status': 0, 'error': str(e)}


def _sub():
    return get_subscription_id()
KQL_BY_TYPE = {
    'Microsoft.Sql/servers': (
        'AzureDiagnostics\n| where TimeGenerated > ago({lookback}h)\n'
        '| where ResourceProvider == "MICROSOFT.SQL"\n'
        '| where Category has "Errors" or Level == "Error"\n'
        '| summarize Count=count() by Message=substring(Message, 0, 200)\n'
        '| order by Count desc | take 10'
    ),
    'Microsoft.Storage/storageAccounts': (
        'StorageBlobLogs\n| where TimeGenerated > ago({lookback}h)\n'
        '| where toint(StatusCode) >= 400 or StatusText has "error"\n'
        '| summarize Count=count() by OperationName, StatusText, ObjectKey=tostring(ObjectKey), Uri=tostring(Uri)\n'
        '| order by Count desc | take 10'
    ),
    'Microsoft.DataFactory/factories': (
        'ADFPipelineRun\n| where TimeGenerated > ago({lookback}h)\n'
        '| where Status == "Failed"\n'
        '| summarize Count=count() by PipelineName, Status\n'
        '| order by Count desc | take 10'
    ),
    'Microsoft.Databricks/workspaces': (
        'AzureDiagnostics\n| where TimeGenerated > ago({lookback}h)\n'
        '| where ResourceProvider == "DATABRICKS"\n'
        '| where Level == "Error"\n'
        '| summarize Count=count() by Message=substring(Message, 0, 200)\n'
        '| order by Count desc | take 10'
    ),
    'Microsoft.KeyVault/vaults': (
        'AzureDiagnostics\n| where TimeGenerated > ago({lookback}h)\n'
        '| where ResourceProvider == "MICROSOFT.KEYVAULT"\n'
        '| where ResultSignature != "OK" or Level == "Error"\n'
        '| summarize Count=count() by OperationName, ResultSignature\n'
        '| order by Count desc | take 10'
    ),
}

KQL_GENERIC = (
    'AzureDiagnostics\n| where TimeGenerated > ago({lookback}h)\n'
    '| where Level == "Error"\n'
    '| summarize Count=count() by Resource, Category\n'
    '| order by Count desc | take 10'
)


def _query_la(workspace_id, kql, token):
    path = f'{workspace_id}/query?api-version=2017-10-01'
    r = _api('POST', path, {'query': kql}, token)
    if 'error' in r:
        return None, r
    return r['body'], None


def _dbx_token():
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env('databricks_token')
    except Exception:
        return os.environ.get('DATABRICKS_TOKEN')


def _dbx_get(workspace_url, path):
    tok = _dbx_token()
    if not tok:
        return None, 'DATABRICKS_TOKEN mancante'
    url = workspace_url.rstrip('/') + path
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + tok)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}: {e.read().decode()[:200]}'
    except Exception as e:
        return None, str(e)


def _check_databricks(res, token):
    rid = res.get('id')
    r = _api('GET', f'{rid}?api-version=2023-02-01', None, token)
    if 'error' in r:
        return {'state_status': 'unknown', 'state_detail': 'impossibile leggere workspace'}
    props = r['body'].get('properties', {})
    ws_url = props.get('workspaceUrl')
    if not ws_url:
        return {'state_status': 'unknown', 'state_detail': 'workspaceUrl assente'}
    if not ws_url.startswith('http'):
        ws_url = 'https://' + ws_url
    clusters, err = _dbx_get(ws_url, '/api/2.0/clusters/list')
    if err:
        return {'state_status': 'unknown', 'state_detail': f'API Databricks: {err}'}
    items = clusters.get('clusters', []) if clusters else []
    bad = []
    for c in items:
        st = c.get('state')
        if st in ('ERROR', 'TERMINATED') and c.get('termination_reason', {}).get('type') == 'CLOUD_FAILURE':
            bad.append({
                'cluster': c.get('cluster_name'),
                'state': st,
                'reason': c.get('termination_reason', {}).get('code'),
                'message': (c.get('state_message') or '')[:200],
            })
        elif st == 'ERROR':
            bad.append({'cluster': c.get('cluster_name'), 'state': st, 'message': (c.get('state_message') or '')[:200]})
    if bad:
        return {'state_status': 'error', 'state_detail': f'{len(bad)} cluster in errore', 'clusters': bad}
    return {'state_status': 'healthy', 'state_detail': f'{len(items)} cluster OK'}


def _check_adf(res, token):
    rid = res.get('id')
    r = _api('GET', f'{rid}/triggers?api-version=2018-06-01', None, token)
    if 'error' in r:
        return {'state_status': 'unknown', 'state_detail': 'impossibile leggere trigger'}
    trigs = r['body'].get('value', [])
    stopped = [t.get('name') for t in trigs if t.get('properties', {}).get('runtimeState') == 'Stopped']
    if stopped:
        return {'state_status': 'warning', 'state_detail': f'{len(stopped)} trigger stopped', 'triggers': stopped}
    return {'state_status': 'healthy', 'state_detail': f'{len(trigs)} trigger OK'}


def _check_sql_server(res, token):
    rid = res.get('id')
    r = _api('GET', f'{rid}/databases?api-version=2021-11-01', None, token)
    if 'error' in r:
        return {'state_status': 'unknown', 'state_detail': 'impossibile leggere DB'}
    dbs = r['body'].get('value', [])
    bad = [d.get('name') for d in dbs if d.get('properties', {}).get('status') not in ('Online', None)]
    if bad:
        return {'state_status': 'error', 'state_detail': f'DB non online: {bad}'}
    return {'state_status': 'healthy', 'state_detail': f'{len(dbs)} DB online'}


def _check_sql_database(res, token):
    rid = res.get('id')
    r = _api('GET', f'{rid}?api-version=2021-11-01', None, token)
    if 'error' in r:
        return {'state_status': 'unknown', 'state_detail': 'impossibile leggere DB'}
    st = r['body'].get('properties', {}).get('status')
    if st == 'Online':
        return {'state_status': 'healthy', 'state_detail': f'DB {st}'}
    if st:
        return {'state_status': 'error', 'state_detail': f'DB {st}'}
    return {'state_status': 'unknown', 'state_detail': 'stato DB non disponibile'}


def _check_storage(res, token):
    rid = res.get('id')
    r = _api('GET', f'{rid}?api-version=2023-01-01', None, token)
    if 'error' in r:
        return {'state_status': 'unknown', 'state_detail': 'impossibile leggere storage'}
    props = r['body'].get('properties', {})
    st = props.get('provisioningState')
    if st == 'Succeeded':
        return {'state_status': 'healthy', 'state_detail': f'provisioningState={st}'}
    if st:
        return {'state_status': 'warning', 'state_detail': f'provisioningState={st}'}
    return {'state_status': 'unknown', 'state_detail': 'stato non disponibile'}


def _check_vnet(res, token):
    rid = res.get('id')
    r = _api('GET', f'{rid}?api-version=2023-09-01', None, token)
    if 'error' in r:
        return {'state_status': 'unknown', 'state_detail': 'impossibile leggere VNet'}
    props = r['body'].get('properties', {})
    st = props.get('provisioningState')
    subnets = props.get('subnets', [])
    if st == 'Succeeded':
        return {'state_status': 'healthy', 'state_detail': f'{len(subnets)} subnet, provisioningState={st}'}
    if st:
        return {'state_status': 'warning', 'state_detail': f'provisioningState={st}'}
    return {'state_status': 'unknown', 'state_detail': 'stato non disponibile'}


STATE_CHECKS = {
    'Microsoft.Databricks/workspaces': _check_databricks,
    'Microsoft.DataFactory/factories': _check_adf,
    'Microsoft.Sql/servers/databases': _check_sql_database,
    'Microsoft.Sql/servers': _check_sql_server,
    'Microsoft.Storage/storageAccounts': _check_storage,
    'Microsoft.Network/virtualNetworks': _check_vnet,
}


def _esc_html(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))


def _build_dashboard(rg, workspace, lookback, summary, results, generated_at):
    status_color = {'healthy': '#28a745', 'warning': '#ffc107', 'error': '#dc3545', 'unknown': '#6c757d'}
    status_icon = {'healthy': 'OK', 'warning': 'WARN', 'error': 'ERR', 'unknown': '?'}
    cards = []
    for r in results:
        st = r.get('status', 'unknown')
        color = status_color.get(st, '#6c757d')
        icon = status_icon.get(st, '?')
        errs = r.get('top_errors', [])
        rows_html = ''
        for e in errs:
            cells = ''.join(f'<td>{_esc_html(v)}</td>' for v in e.values())
            rows_html += f'<tr>{cells}</tr>'
        headers = ''.join(f'<th>{_esc_html(k)}</th>' for k in errs[0].keys()) if errs else ''
        table = f'<table class="err"><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>' if rows_html else ''
        state_html = ''
        if r.get('state_detail'):
            sc = status_color.get(r.get('state_status', 'unknown'), '#6c757d')
            state_html = f'<div class="state" style="border-left:4px solid {sc};"><b>Stato:</b> {_esc_html(r.get("state_detail"))}'
            for c in r.get('clusters', []):
                state_html += f'<div class="cl">cluster <b>{_esc_html(c.get("cluster"))}</b>: {_esc_html(c.get("state"))} — {_esc_html(c.get("reason") or "")} — {_esc_html(c.get("message") or "")}</div>'
            state_html += '</div>'
        q = _esc_html(r.get('query', ''))
        cards.append(f'''<div class="card" style="border-left: 6px solid {color};">
          <div class="card-head"><span class="badge" style="background:{color};">{icon}</span>
          <span class="rname">{_esc_html(r.get('resource'))}</span>
          <span class="rtype">{_esc_html(r.get('type'))}</span>
          <span class="count">{r.get('error_count', 0)} errori log</span></div>
          {state_html}{table}
          <details><summary>Query KQL</summary><pre>{q}</pre></details></div>''')
    html = f'''<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<title>Health Dashboard - {_esc_html(rg)}</title><style>
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; background:#f4f6f8; color:#222; }}
header {{ background:#1f2937; color:#fff; padding:20px 30px; }} header h1 {{ margin:0 0 6px 0; font-size:22px; }}
header .meta {{ font-size:13px; opacity:.8; }}
.summary {{ display:flex; gap:16px; padding:20px 30px; }}
.kpi {{ flex:1; background:#fff; border-radius:10px; padding:18px; box-shadow:0 1px 4px rgba(0,0,0,.08); text-align:center; }}
.kpi .num {{ font-size:32px; font-weight:700; }} .kpi .lbl {{ font-size:13px; color:#666; text-transform:uppercase; }}
.cards {{ padding:0 30px 40px 30px; display:grid; gap:14px; }}
.card {{ background:#fff; border-radius:10px; padding:16px 18px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
.card-head {{ display:flex; align-items:center; gap:12px; }}
.badge {{ color:#fff; font-weight:700; font-size:12px; padding:4px 10px; border-radius:6px; }}
.rname {{ font-weight:600; font-size:16px; }} .rtype {{ color:#888; font-size:12px; }} .count {{ margin-left:auto; font-weight:600; color:#444; }}
table.err {{ width:100%; border-collapse:collapse; margin-top:12px; font-size:13px; }}
table.err th, table.err td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #eee; }}
table.err th {{ background:#fafafa; color:#555; }}
.state {{ margin-top:10px; padding:8px 12px; background:#fafafa; font-size:13px; }}
.cl {{ margin-top:4px; color:#b91c1c; }}
details {{ margin-top:10px; }} details summary {{ cursor:pointer; color:#2563eb; font-size:13px; }}
pre {{ background:#0f172a; color:#e2e8f0; padding:12px; border-radius:8px; overflow-x:auto; font-size:12px; }}
</style></head><body>
<header><h1>Health Dashboard &mdash; {_esc_html(rg)}</h1>
<div class="meta">Workspace: {_esc_html(workspace)} &middot; Lookback: {lookback}h &middot; Generato: {_esc_html(generated_at)}</div></header>
<div class="summary">
<div class="kpi"><div class="num" style="color:#28a745">{summary.get('healthy',0)}</div><div class="lbl">Healthy</div></div>
<div class="kpi"><div class="num" style="color:#ffc107">{summary.get('warning',0)}</div><div class="lbl">Warning</div></div>
<div class="kpi"><div class="num" style="color:#dc3545">{summary.get('error',0)}</div><div class="lbl">Error</div></div>
<div class="kpi"><div class="num" style="color:#6c757d">{summary.get('unknown',0)}</div><div class="lbl">Unknown</div></div>
</div><div class="cards">{''.join(cards)}</div></body></html>'''
    return html


def run(**kwargs):
    rg = kwargs.get('resource_group')
    workspace_name = kwargs.get('workspace_name')
    lookback = kwargs.get('lookback_hours', 24)
    filter_resources = kwargs.get('resources') or []
    generate_dashboard = kwargs.get('generate_dashboard', True)
    dashboard_path = kwargs.get('dashboard_path', 'docs/health-dashboard.html')
    include_state_checks = kwargs.get('include_state_checks', True)
    if not rg:
        return {'ok': False, 'error': 'resource_group mancante'}
    token, err = _token()
    if err:
        return {'ok': False, 'error': err}
    try:
        sub = _sub()
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    base = f'/subscriptions/{sub}/resourceGroups/{rg}'
    if not workspace_name:
        rw = _api('GET', f'{base}/providers/Microsoft.OperationalInsights/workspaces?api-version=2022-10-01', None, token)
        if 'error' in rw:
            return {'ok': False, 'stage': 'find_workspace', 'error': rw['error']}
        wss = rw['body'].get('value', [])
        if not wss:
            return {'ok': False, 'error': 'Nessun Log Analytics workspace'}
        workspace_name = wss[0].get('name')
    workspace_id = f'{base}/providers/Microsoft.OperationalInsights/workspaces/{workspace_name}'
    r = _api('GET', f'{base}/resources?api-version=2021-04-01', None, token)
    if 'error' in r:
        return {'ok': False, 'stage': 'list_resources', 'error': r['error']}
    resources = r['body'].get('value', [])
    if filter_resources:
        resources = [x for x in resources if x.get('name') in filter_resources]
    results = []
    for res in resources:
        rtype = res.get('type', '')
        rname = res.get('name', '')
        kql_tpl = None
        for k, v in KQL_BY_TYPE.items():
            if rtype.lower().startswith(k.lower()):
                kql_tpl = v
                break
        if not kql_tpl:
            kql_tpl = KQL_GENERIC
        kql = kql_tpl.replace('{lookback}', str(lookback))
        data, qerr = _query_la(workspace_id, kql, token)
        rows = []
        if not qerr and data:
            tables = data.get('tables', [])
            if tables:
                cols = [c.get('name') for c in tables[0].get('columns', [])]
                for row in tables[0].get('rows', []):
                    rows.append(dict(zip(cols, row)))
        total = sum(int(rr.get('Count', 0)) for rr in rows if isinstance(rr.get('Count'), (int, float)))
        log_status = 'healthy' if total == 0 else ('warning' if total < 10 else 'error')
        entry = {'resource': rname, 'type': rtype, 'log_status': log_status, 'error_count': total, 'top_errors': rows[:5], 'query': kql}
        if qerr:
            entry['log_error'] = qerr.get('error')
        if include_state_checks:
            for k, fn in STATE_CHECKS.items():
                if rtype.lower().startswith(k.lower()):
                    sc = fn(res, token)
                    entry.update(sc)
                    break
        sev = {'healthy': 0, 'warning': 1, 'error': 2, 'unknown': 1}
        ls = entry.get('log_status', 'healthy')
        ss = entry.get('state_status', 'healthy')
        entry['status'] = ls if sev.get(ls, 0) >= sev.get(ss, 0) else ss
        results.append(entry)
    summary = {
        'healthy': sum(1 for x in results if x['status'] == 'healthy'),
        'warning': sum(1 for x in results if x['status'] == 'warning'),
        'error': sum(1 for x in results if x['status'] == 'error'),
        'unknown': sum(1 for x in results if x['status'] == 'unknown'),
    }
    out = {'ok': True, 'resource_group': rg, 'workspace': workspace_name, 'lookback_hours': lookback, 'summary': summary, 'results': results}
    if generate_dashboard:
        generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        out['dashboard_path'] = dashboard_path
        out['dashboard_html'] = _build_dashboard(rg, workspace_name, lookback, summary, results, generated_at)
    return out

MANIFEST = {
    "tools": [
        {"name": "monitor.health_probe", "doc": "[READ] Health probe IBRIDO su tutte le risorse Azure: combina (1) query KQL su Log Analytics per errori nei log e (2) CHECK DI STATO NATIVI per tipo risorsa (Databricks clusters, ADF triggers, SQL servers/databases, Storage accounts, VNet). Rileva stati reali come cluster TERMINATED/ERROR, DB non online, storage non disponibili. Genera anche una DASHBOARD HTML consultabile su browser. Args: {\"resource_group\": str, \"workspace_name\": str (opz), \"lookback_hours\": int (opz, default 24), \"resources\": [str] (opz), \"severity\": str (opz), \"generate_dashboard\": bool (opz, default true), \"dashboard_path\": str (opz), \"include_state_checks\": bool (opz, default true)}. Restituisce: riepilogo salute, risultati per risorsa (log + stato), HTML dashboard.", "write": False, "entrypoint": "run"}
    ]
}
