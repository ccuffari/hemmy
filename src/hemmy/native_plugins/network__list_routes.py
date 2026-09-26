# Plugin auto-generato per il tool 'network.list_routes'.
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
    resource_group = kwargs.get('resource_group')
    route_table_name = kwargs.get('route_table_name')
    vnet_name = kwargs.get('vnet_name')
    subnet_name = kwargs.get('subnet_name')
    if not resource_group:
        return {'ok': False, 'error': 'resource_group obbligatorio'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Network')
    try:
        if vnet_name and subnet_name:
            url = (f'{base}/virtualNetworks/{vnet_name}/subnets/{subnet_name}'
                   f'?api-version=2023-09-01')
            sdata = _get(url, token)
            rt = sdata.get('properties', {}).get('routeTable', {}).get('id')
            return {'ok': True, 'mode': 'subnet_route_table', 'vnet': vnet_name,
                    'subnet': subnet_name, 'route_table_id': rt}

        if route_table_name:
            rdata = _get(f'{base}/routeTables/{route_table_name}/routes?api-version=2023-09-01', token)
            routes = []
            for r in rdata.get('value', []):
                props = r.get('properties', {})
                routes.append({
                    'name': r.get('name'),
                    'address_prefix': props.get('addressPrefix'),
                    'next_hop_type': props.get('nextHopType'),
                    'next_hop_ip': props.get('nextHopIpAddress'),
                })
            return {'ok': True, 'mode': 'route_table', 'route_table': route_table_name,
                    'count': len(routes), 'routes': routes}

        tdata = _get(f'{base}/routeTables?api-version=2023-09-01', token)
        tables = []
        for t in tdata.get('value', []):
            props = t.get('properties', {})
            routes = []
            for r in props.get('routes', []):
                rp = r.get('properties', {})
                routes.append({
                    'name': r.get('name'),
                    'address_prefix': rp.get('addressPrefix'),
                    'next_hop_type': rp.get('nextHopType'),
                    'next_hop_ip': rp.get('nextHopIpAddress'),
                })
            tables.append({
                'name': t.get('name'),
                'disable_bgp_route_propagation': props.get('disableBgpRoutePropagation'),
                'routes': routes,
            })
        return {'ok': True, 'mode': 'all_route_tables', 'count': len(tables), 'route_tables': tables}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "network.list_routes", "doc": "[READ] Elenca le route table (UDR) e le route di un RG, oppure le route effettive di una subnet. Args: {\"resource_group\": str, \"route_table_name\": str (opz), \"vnet_name\": str (opz), \"subnet_name\": str (opz)}. Ritorna route table, route e next hop.", "write": False, "entrypoint": "run"}
    ]
}
