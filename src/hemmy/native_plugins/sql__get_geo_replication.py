# Plugin auto-generato per il tool 'sql.get_geo_replication'.
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
    if not resource_group or not server_name or not database_name:
        return {'ok': False, 'error': 'resource_group, server_name, database_name obbligatori'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Sql/servers/{server_name}/databases/{database_name}')
    try:
        repl = _get(f'{base}/replicationLinks?api-version=2021-11-01', token)
        links = []
        for r in repl.get('value', []):
            props = r.get('properties', {})
            links.append({
                'name': r.get('name'),
                'role': props.get('role'),
                'partner_server': props.get('partnerServer'),
                'partner_database': props.get('partnerDatabase'),
                'replication_state': props.get('replicationState'),
                'percent_complete': props.get('percentComplete'),
                'replication_state_desc': props.get('replicationStateDesc'),
            })
        return {'ok': True, 'database': database_name, 'count': len(links), 'replication_links': links}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_geo_replication", "doc": "[READ] Legge lo stato della geo-replica di un Azure SQL Database (repliche secondarie, stato, ruolo). Args: {\"resource_group\": str (opz), \"server_name\": str (opz), \"database_name\": str (opz)}. Ritorna repliche e stato.", "write": False, "entrypoint": "run"}
    ]
}
