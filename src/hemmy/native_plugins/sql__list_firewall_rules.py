# Plugin auto-generato per il tool 'sql.list_firewall_rules'.
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
    try:
        ip_rules = _get(f'{base}/firewallRules?api-version=2021-11-01', token)
        vnet_rules = _get(f'{base}/virtualNetworkRules?api-version=2021-11-01', token)
        return {
            'ok': True,
            'server': server_name,
            'ip_rules': [{'name': r.get('name'),
                          'start_ip': r.get('properties', {}).get('startIpAddress'),
                          'end_ip': r.get('properties', {}).get('endIpAddress')}
                         for r in ip_rules.get('value', [])],
            'vnet_rules': [{'name': r.get('name'),
                            'subnet_id': r.get('properties', {}).get('virtualNetworkSubnetId'),
                            'state': r.get('properties', {}).get('state')}
                           for r in vnet_rules.get('value', [])],
        }
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.list_firewall_rules", "doc": "[READ] Elenca le regole firewall di un Azure SQL Server (IP rules + virtual network rules). Args: {\"resource_group\": str (opz, default da env), \"server_name\": str (opz, default da env)}. Ritorna regole IP e regole VNet.", "write": False, "entrypoint": "run"}
    ]
}
