# Plugin auto-generato per il tool 'sql.audit'.
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
    srv = p.get('server_name')
    db = p.get('database_name')
    if not rg or not srv:
        return {'ok': False, 'error': 'resource_group e server_name richiesti'}
    sb = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{srv}'
    if action == 'get_server_audit':
        r = _req('GET', f'{sb}/auditingSettings/default?api-version=2021-11-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'state': pr.get('state'), 'storage_endpoint': pr.get('storageEndpoint'), 'retention_days': pr.get('retentionDays'), 'audit_actions': pr.get('auditActionsAndGroups'), 'is_azure_monitor_target_enabled': pr.get('isAzureMonitorTargetEnabled')}
    if action == 'get_db_audit':
        if not db:
            return {'ok': False, 'error': 'database_name richiesto'}
        r = _req('GET', f'{sb}/databases/{db}/auditingSettings/default?api-version=2021-11-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'state': pr.get('state'), 'storage_endpoint': pr.get('storageEndpoint'), 'retention_days': pr.get('retentionDays'), 'is_azure_monitor_target_enabled': pr.get('isAzureMonitorTargetEnabled')}
    if action == 'get_atp':
        if not db:
            return {'ok': False, 'error': 'database_name richiesto'}
        r = _req('GET', f'{sb}/databases/{db}/advancedThreatProtectionSettings/default?api-version=2021-11-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'state': pr.get('state'), 'creation_time': pr.get('creationTime')}
    if action == 'get_va':
        if not db:
            return {'ok': False, 'error': 'database_name richiesto'}
        r = _req('GET', f'{sb}/databases/{db}/vulnerabilityAssessments/default?api-version=2021-11-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'state': pr.get('recurringScans', {}).get('isEnabled'), 'storage_container_path': pr.get('storageContainerPath'), 'recurring_scans': pr.get('recurringScans')}
    if action == 'get_tde':
        if not db:
            return {'ok': False, 'error': 'database_name richiesto'}
        r = _req('GET', f'{sb}/databases/{db}/transparentDataEncryption/current?api-version=2021-11-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'state': pr.get('state')}
    if action == 'list_audit_logs':
        r = _req('GET', f'{sb}/auditingSettings?api-version=2021-11-01', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'state': x.get('properties', {}).get('state')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'settings': out}
    if action == 'get_security_summary':
        out = {}
        r = _req('GET', f'{sb}/auditingSettings/default?api-version=2021-11-01', token)
        out['server_audit'] = r['data'].get('properties', {}).get('state') if r.get('ok') else 'n/a'
        if db:
            r = _req('GET', f'{sb}/databases/{db}/auditingSettings/default?api-version=2021-11-01', token)
            out['db_audit'] = r['data'].get('properties', {}).get('state') if r.get('ok') else 'n/a'
            r = _req('GET', f'{sb}/databases/{db}/advancedThreatProtectionSettings/default?api-version=2021-11-01', token)
            out['atp'] = r['data'].get('properties', {}).get('state') if r.get('ok') else 'n/a'
            r = _req('GET', f'{sb}/databases/{db}/transparentDataEncryption/current?api-version=2021-11-01', token)
            out['tde'] = r['data'].get('properties', {}).get('state') if r.get('ok') else 'n/a'
            r = _req('GET', f'{sb}/databases/{db}/vulnerabilityAssessments/default?api-version=2021-11-01', token)
            out['va'] = r['data'].get('properties', {}).get('recurringScans', {}).get('isEnabled') if r.get('ok') else 'n/a'
        return {'ok': True, 'action': action, 'summary': out}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "sql.audit", "doc": "[WRITE] Gestione sicurezza SQL: auditing, Advanced Threat Protection, Vulnerability Assessment, Transparent Data Encryption (TDE). Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: get_server_audit, get_db_audit, get_atp, get_va, get_tde, list_audit_logs, get_security_summary.", "write": True, "entrypoint": "run"}
    ]
}
