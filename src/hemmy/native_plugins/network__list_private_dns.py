# Plugin auto-generato per il tool 'network.list_private_dns'.
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
    zone_name = kwargs.get('zone_name')
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    try:
        if resource_group:
            base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
                    f'/providers/Microsoft.Network/privateDnsZones')
        else:
            base = (f'https://management.azure.com/subscriptions/{sub}'
                    f'/providers/Microsoft.Network/privateDnsZones')

        if zone_name and resource_group:
            zbase = f'{base}/{zone_name}'
            recs = _get(f'{zbase}/A?api-version=2020-06-01', token)
            links = _get(f'{zbase}/virtualNetworkLinks?api-version=2020-06-01', token)
            return {
                'ok': True, 'mode': 'zone_detail', 'zone': zone_name,
                'a_records': [{'name': r.get('name'), 'ipv4': r.get('properties', {}).get('aRecords')}
                              for r in recs.get('value', [])],
                'vnet_links': [{'name': l.get('name'),
                                'vnet': l.get('properties', {}).get('virtualNetwork', {}).get('id'),
                                'registration_enabled': l.get('properties', {}).get('registrationEnabled')}
                               for l in links.get('value', [])],
            }

        zdata = _get(f'{base}?api-version=2020-06-01', token)
        zones = [{'name': z.get('name'), 'record_sets': z.get('properties', {}).get('numberOfRecordSets'),
                  'max_records': z.get('properties', {}).get('maxNumberOfRecordSets')}
                 for z in zdata.get('value', [])]
        return {'ok': True, 'mode': 'list_zones', 'count': len(zones), 'zones': zones}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "network.list_private_dns", "doc": "[READ] Elenca le Private DNS Zone e i record, oppure le zone collegate a una VNet. Args: {\"resource_group\": str (opz), \"zone_name\": str (opz), \"vnet_id\": str (opz)}. Ritorna zone, record e virtual network links.", "write": False, "entrypoint": "run"}
    ]
}
