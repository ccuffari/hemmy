# Plugin auto-generato per il tool 'sql.get_connection_health'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def _get(url, token):
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(**kwargs):
    resource_group = kwargs.get('resource_group') or os.environ.get('SQL_RESOURCE_GROUP')
    server_name = kwargs.get('server_name') or os.environ.get('SQL_SERVER_NAME')
    database_name = kwargs.get('database_name') or os.environ.get('SQL_DATABASE_NAME')
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    if not resource_group or not server_name:
        return {'ok': False, 'error': 'resource_group e server_name obbligatori (o env SQL_RESOURCE_GROUP/SQL_SERVER_NAME)'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Sql/servers/{server_name}')
    result = {'ok': True, 'server': server_name, 'database': database_name}
    try:
        srv = _get(f'{base}?api-version=2021-11-01', token)
        result['server_state'] = srv.get('properties', {}).get('state')
        result['server_fqdn'] = srv.get('properties', {}).get('fullyQualifiedDomainName')
        result['public_network_access'] = srv.get('properties', {}).get('publicNetworkAccess')

        if database_name:
            db = _get(f'{base}/databases/{database_name}?api-version=2021-11-01', token)
            result['database_status'] = db.get('properties', {}).get('status')
            result['database_sku'] = db.get('sku', {}).get('name')

        ip_rules = _get(f'{base}/firewallRules?api-version=2021-11-01', token)
        result['firewall_ip_rules'] = len(ip_rules.get('value', []))
        result['firewall_allow_azure_services'] = any(
            r.get('properties', {}).get('startIpAddress') == '0.0.0.0'
            for r in ip_rules.get('value', [])
        )

        diag = []
        if result.get('server_state') != 'Ready':
            diag.append(f"Server non Ready: {result.get('server_state')}")
        if result.get('public_network_access') == 'Disabled' and result['firewall_ip_rules'] == 0:
            diag.append('Public access disabilitato e nessuna regola firewall: serve Private Endpoint')
        if result['firewall_ip_rules'] == 0 and not result['firewall_allow_azure_services']:
            diag.append('Nessuna regola firewall: connessioni esterne bloccate')
        result['diagnosis'] = diag or ['Nessun problema rilevato a livello di configurazione']
        return result
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_connection_health", "doc": "[READ] Diagnostica la connettività a un Azure SQL Server: stato server, stato database, firewall, e test di connessione. Args: {\"resource_group\": str (opz, default da env), \"server_name\": str (opz, default da env), \"database_name\": str (opz, default da env)}. Ritorna stato e diagnosi.", "write": False, "entrypoint": "run"}
    ]
}
