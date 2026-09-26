# Plugin auto-generato per il tool 'monitor.list_action_groups'.
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
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    if resource_group:
        base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
                f'/providers/Microsoft.Insights/actionGroups')
    else:
        base = (f'https://management.azure.com/subscriptions/{sub}'
                f'/providers/Microsoft.Insights/actionGroups')
    try:
        data = _get(f'{base}?api-version=2023-01-01', token)
        groups = []
        for g in data.get('value', []):
            props = g.get('properties', {})
            receivers = []
            for rtype in ('emailReceivers', 'smsReceivers', 'webhookReceivers',
                          'itsmReceivers', 'logicAppReceivers', 'automationRunbookReceivers'):
                for r in props.get(rtype, []):
                    receivers.append({'type': rtype, 'name': r.get('name'),
                                      'address': r.get('emailAddress') or r.get('phoneNumber')
                                      or r.get('serviceUri') or r.get('webhookUrl')})
            groups.append({
                'name': g.get('name'),
                'group_short_name': props.get('groupShortName'),
                'enabled': props.get('enabled'),
                'receivers': receivers,
            })
        return {'ok': True, 'count': len(groups), 'action_groups': groups}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "monitor.list_action_groups", "doc": "[READ] Elenca gli Action Group di un resource group (o subscription) con i loro receiver (email, webhook, SMS, ecc.). Args: {\"resource_group\": str (opz)}. Ritorna action group e receiver.", "write": False, "entrypoint": "run"}
    ]
}
