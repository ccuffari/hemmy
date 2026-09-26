# Plugin auto-generato per il tool 'network.list_peerings'.
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
    vnet_name = kwargs.get('vnet_name')
    if not resource_group:
        return {'ok': False, 'error': 'resource_group obbligatorio'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Network/virtualNetworks')
    try:
        if vnet_name:
            vnets = [vnet_name]
        else:
            data = _get(f'{base}?api-version=2023-09-01', token)
            vnets = [v['name'] for v in data.get('value', [])]
        result = []
        for vn in vnets:
            try:
                pdata = _get(f'{base}/{vn}/virtualNetworkPeerings?api-version=2023-09-01', token)
            except Exception:
                continue
            for p in pdata.get('value', []):
                props = p.get('properties', {})
                result.append({
                    'vnet': vn,
                    'peering_name': p.get('name'),
                    'state': props.get('peeringState'),
                    'remote_vnet': props.get('remoteVirtualNetwork', {}).get('id'),
                    'allow_forwarded_traffic': props.get('allowForwardedTraffic'),
                    'allow_gateway_transit': props.get('allowGatewayTransit'),
                    'use_remote_gateways': props.get('useRemoteGateways'),
                })
        return {'ok': True, 'count': len(result), 'peerings': result}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "network.list_peerings", "doc": "[READ] Elenca i VNet peering di una Virtual Network (o di tutte le VNet di un RG). Args: {\"resource_group\": str, \"vnet_name\": str (opz)}. Ritorna peering, stato, VNet remota.", "write": False, "entrypoint": "run"}
    ]
}
