# Plugin auto-generato per il tool 'eventgrid.manage'.
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


def _list(url, token):
    r = _req('GET', url, token)
    if not r.get('ok'):
        return r
    return {'ok': True, 'value': r['data'].get('value', [])}


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
    base = f'https://management.azure.com/subscriptions/{sub}' + (f'/resourceGroups/{rg}' if rg else '')
    if action == 'list_eventgrid_topics':
        r = _list(f'{base}/providers/Microsoft.EventGrid/topics?api-version=2022-06-15', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'location': x.get('location'), 'provisioning_state': x.get('properties', {}).get('provisioningState'), 'endpoint': x.get('properties', {}).get('endpoint'), 'public_network_access': x.get('properties', {}).get('publicNetworkAccess')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'topics': out}
    if action == 'list_eventgrid_subscriptions':
        topic = p.get('topic_name')
        if not rg or not topic:
            return {'ok': False, 'error': 'resource_group e topic_name richiesti'}
        r = _list(f'{base}/providers/Microsoft.EventGrid/topics/{topic}/eventSubscriptions?api-version=2022-06-15', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'destination': x.get('properties', {}).get('destination', {}).get('endpointType'), 'event_delivery_schema': x.get('properties', {}).get('eventDeliverySchema'), 'provisioning_state': x.get('properties', {}).get('provisioningState')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'subscriptions': out}
    if action == 'list_servicebus_namespaces':
        r = _list(f'{base}/providers/Microsoft.ServiceBus/namespaces?api-version=2022-10-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'location': x.get('location'), 'sku': x.get('sku', {}).get('name'), 'provisioning_state': x.get('properties', {}).get('provisioningState'), 'service_bus_endpoint': x.get('properties', {}).get('serviceBusEndpoint')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'namespaces': out}
    if action == 'list_servicebus_queues':
        ns = p.get('namespace_name')
        if not rg or not ns:
            return {'ok': False, 'error': 'resource_group e namespace_name richiesti'}
        r = _list(f'{base}/providers/Microsoft.ServiceBus/namespaces/{ns}/queues?api-version=2022-10-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'status': x.get('properties', {}).get('status'), 'message_count': x.get('properties', {}).get('messageCount'), 'size_in_bytes': x.get('properties', {}).get('sizeInBytes'), 'max_size_mb': x.get('properties', {}).get('maxSizeInMegabytes'), 'dead_letter_count': x.get('properties', {}).get('countDetails', {}).get('deadLetterMessageCount')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'queues': out}
    if action == 'list_servicebus_topics':
        ns = p.get('namespace_name')
        if not rg or not ns:
            return {'ok': False, 'error': 'resource_group e namespace_name richiesti'}
        r = _list(f'{base}/providers/Microsoft.ServiceBus/namespaces/{ns}/topics?api-version=2022-10-01-preview', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'status': x.get('properties', {}).get('status'), 'subscription_count': x.get('properties', {}).get('subscriptionCount'), 'size_in_bytes': x.get('properties', {}).get('sizeInBytes')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'topics': out}
    if action == 'list_eventhubs_namespaces':
        r = _list(f'{base}/providers/Microsoft.EventHub/namespaces?api-version=2024-01-01', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'location': x.get('location'), 'sku': x.get('sku', {}).get('name'), 'provisioning_state': x.get('properties', {}).get('provisioningState'), 'status': x.get('properties', {}).get('status')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'namespaces': out}
    if action == 'list_eventhubs':
        ns = p.get('namespace_name')
        if not rg or not ns:
            return {'ok': False, 'error': 'resource_group e namespace_name richiesti'}
        r = _list(f'{base}/providers/Microsoft.EventHub/namespaces/{ns}/eventhubs?api-version=2024-01-01', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'partition_count': x.get('properties', {}).get('partitionCount'), 'message_retention_days': x.get('properties', {}).get('messageRetentionInDays'), 'status': x.get('properties', {}).get('status')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'eventhubs': out}
    if action == 'list_consumer_groups':
        ns = p.get('namespace_name')
        eh = p.get('eventhub_name')
        if not rg or not ns or not eh:
            return {'ok': False, 'error': 'resource_group, namespace_name, eventhub_name richiesti'}
        r = _list(f'{base}/providers/Microsoft.EventHub/namespaces/{ns}/eventhubs/{eh}/consumergroups?api-version=2024-01-01', token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'created_at': x.get('properties', {}).get('createdAt'), 'user_metadata': x.get('properties', {}).get('userMetadata')} for x in r['value']]
        return {'ok': True, 'action': action, 'count': len(out), 'consumer_groups': out}
    if action == 'get_eventing_summary':
        out = {}
        for k, path in (('eventgrid_topics', 'Microsoft.EventGrid/topics'), ('servicebus_namespaces', 'Microsoft.ServiceBus/namespaces'), ('eventhubs_namespaces', 'Microsoft.EventHub/namespaces')):
            r = _list(f'{base}/providers/{path}?api-version=2021-04-01', token)
            out[k] = len(r.get('value', [])) if r.get('ok') else 'n/a'
        return {'ok': True, 'action': action, 'summary': out}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "eventgrid.manage", "doc": "[WRITE] Gestione servizi di eventing Azure: Event Grid (topic, subscription), Service Bus (namespace, code, topic), Event Hubs (namespace, hub, consumer group). Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_eventgrid_topics, list_eventgrid_subscriptions, list_servicebus_namespaces, list_servicebus_queues, list_servicebus_topics, list_eventhubs_namespaces, list_eventhubs, list_consumer_groups, get_eventing_summary.", "write": True, "entrypoint": "run"}
    ]
}
