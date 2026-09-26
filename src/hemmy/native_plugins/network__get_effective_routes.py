# Plugin auto-generato per il tool 'network.get_effective_routes'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def _post(url, token, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _get(url, token):
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(**kwargs):
    resource_group = kwargs.get('resource_group')
    nic_name = kwargs.get('nic_name')
    if not resource_group or not nic_name:
        return {'ok': False, 'error': 'resource_group e nic_name obbligatori'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    nic_id = (f'/subscriptions/{sub}/resourceGroups/{resource_group}'
              f'/providers/Microsoft.Network/networkInterfaces/{nic_name}')
    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Network/networkWatchers')
    try:
        watchers = _get(f'{base}?api-version=2023-09-01', token)
        if not watchers.get('value'):
            return {'ok': False, 'error': 'nessun Network Watcher nel RG: creane uno per usare effective routes'}
        watcher = watchers['value'][0]['name']
        rurl = f'{base}/{watcher}/effectiveRouteTable?api-version=2023-09-01'
        result = _post(rurl, token, {'targetResourceId': nic_id})
        routes = []
        for r in result.get('value', []):
            routes.append({
                'name': r.get('name'),
                'source': r.get('source'),
                'state': r.get('state'),
                'address_prefixes': r.get('addressPrefix'),
                'next_hop_type': r.get('nextHopType'),
                'next_hop_ip': r.get('nextHopIpAddress'),
            })
        return {'ok': True, 'nic': nic_name, 'count': len(routes), 'routes': routes}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "network.get_effective_routes", "doc": "[READ] Elenca le route effettive di una NIC (Network Watcher) — mostra come Azure instrada il traffico (system routes + UDR + BGP). Args: {\"resource_group\": str, \"nic_name\": str}. Ritorna route effettive con next hop.", "write": False, "entrypoint": "run"}
    ]
}
