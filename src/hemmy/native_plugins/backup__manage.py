# Plugin auto-generato per il tool 'backup.manage'.
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
    if action == 'list_sql_backup_policies':
        if not rg or not srv or not db:
            return {'ok': False, 'error': 'resource_group, server_name, database_name richiesti'}
        url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{srv}/databases/{db}/backupShortTermRetentionPolicies?api-version=2021-11-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'retention_days': x.get('properties', {}).get('retentionDays')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'policies': out}
    if action == 'get_sql_backup_policy':
        if not rg or not srv or not db:
            return {'ok': False, 'error': 'resource_group, server_name, database_name richiesti'}
        url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{srv}/databases/{db}/backupShortTermRetentionPolicies/default?api-version=2021-11-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'policy': r['data']}
    if action == 'list_sql_restore_points':
        if not rg or not srv or not db:
            return {'ok': False, 'error': 'resource_group, server_name, database_name richiesti'}
        url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{srv}/databases/{db}/restorePoints?api-version=2021-11-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'restore_point_type': x.get('properties', {}).get('restorePointType'), 'restore_point_creation_date': x.get('properties', {}).get('restorePointCreationDate'), 'earliest_restore_date': x.get('properties', {}).get('earliestRestoreDate')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'restore_points': out}
    if action == 'list_recovery_vaults':
        url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.RecoveryServices/vaults?api-version=2023-04-01'
               if rg else f'https://management.azure.com/subscriptions/{sub}/providers/Microsoft.RecoveryServices/vaults?api-version=2023-04-01')
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'location': x.get('location'), 'sku': x.get('sku', {}).get('name'), 'provisioning_state': x.get('properties', {}).get('provisioningState')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'vaults': out}
    if action == 'list_backup_items':
        vault = p.get('vault_name')
        if not rg or not vault:
            return {'ok': False, 'error': 'resource_group e vault_name richiesti'}
        url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.RecoveryServices/vaults/{vault}/backupProtectedItems?api-version=2023-04-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'type': x.get('properties', {}).get('protectedItemType'), 'friendly_name': x.get('properties', {}).get('friendlyName'), 'protection_state': x.get('properties', {}).get('protectionState'), 'last_backup_time': x.get('properties', {}).get('lastBackupTime'), 'health_status': x.get('properties', {}).get('healthStatus')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'items': out}
    if action == 'list_backup_policies':
        vault = p.get('vault_name')
        if not rg or not vault:
            return {'ok': False, 'error': 'resource_group e vault_name richiesti'}
        url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.RecoveryServices/vaults/{vault}/backupPolicies?api-version=2023-04-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'type': x.get('properties', {}).get('backupManagementType'), 'schedule': x.get('properties', {}).get('schedulePolicy'), 'retention': x.get('properties', {}).get('retentionPolicy')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'policies': out}
    if action == 'get_backup_summary':
        url = f'https://management.azure.com/subscriptions/{sub}/providers/Microsoft.RecoveryServices/vaults?api-version=2023-04-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        vaults = r['data'].get('value', [])
        return {'ok': True, 'action': action, 'vault_count': len(vaults), 'vaults': [{'name': x.get('name'), 'rg': x['id'].split('/')[4], 'location': x.get('location')} for x in vaults]}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "backup.manage", "doc": "[WRITE] Gestione backup e disaster recovery: backup policy SQL, restore point, geo-backup, Recovery Services Vault, backup item. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_sql_backup_policies, get_sql_backup_policy, list_sql_restore_points, list_recovery_vaults, list_backup_items, list_backup_policies, get_backup_summary.", "write": True, "entrypoint": "run"}
    ]
}
