# Plugin auto-generato per il tool 'network.check_connectivity'.
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
    source_nic_name = kwargs.get('source_nic_name')
    source_vm_name = kwargs.get('source_vm_name')
    dest_address = kwargs.get('dest_address')
    dest_port = int(kwargs.get('dest_port', 443))
    if not resource_group or not dest_address:
        return {'ok': False, 'error': 'resource_group e dest_address obbligatori'}
    if not source_nic_name and not source_vm_name:
        return {'ok': False, 'error': 'serve source_nic_name oppure source_vm_name'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    if source_nic_name:
        src_id = (f'/subscriptions/{sub}/resourceGroups/{resource_group}'
                  f'/providers/Microsoft.Network/networkInterfaces/{source_nic_name}')
    else:
        src_id = (f'/subscriptions/{sub}/resourceGroups/{resource_group}'
                  f'/providers/Microsoft.Compute/virtualMachines/{source_vm_name}')

    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Network/networkWatchers')
    try:
        watchers = _get(f'{base}?api-version=2023-09-01', token)
        if not watchers.get('value'):
            return {'ok': False, 'error': 'nessun Network Watcher nel RG'}
        watcher = watchers['value'][0]['name']
        url = f'{base}/{watcher}/connectivityCheck?api-version=2023-09-01'
        body = {
            'source': {'resourceId': src_id},
            'destination': {'address': dest_address, 'port': dest_port},
            'protocol': 'Tcp',
            'preferredIPVersion': 'IPv4',
        }
        result = _post(url, token, body)
        conn = result.get('connectionStatus')
        hops = [{'type': h.get('type'), 'id': h.get('id'), 'issues': h.get('issues')}
                for h in result.get('hops', [])]
        return {
            'ok': True,
            'connection_status': conn,
            'avg_latency_ms': result.get('avgLatencyInMs'),
            'min_latency_ms': result.get('minLatencyInMs'),
            'max_latency_ms': result.get('maxLatencyInMs'),
            'probes_sent': result.get('probesSent'),
            'probes_failed': result.get('probesFailed'),
            'hops': hops,
        }
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "network.check_connectivity", "doc": "[READ] Testa la connettività da una VM/NIC verso un endpoint (IP/FQDN) usando Network Watcher. Args: {\"resource_group\": str, \"source_nic_name\": str (opz), \"source_vm_name\": str (opz), \"dest_address\": str, \"dest_port\": int}. Ritorna esito, latenza e hop.", "write": False, "entrypoint": "run"}
    ]
}
