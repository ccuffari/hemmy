# Plugin auto-generato per il tool 'security.list_alerts'.
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
    scope = kwargs.get('scope')
    severity = kwargs.get('severity')
    top = int(kwargs.get('top', 50))
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    scope_id = scope or f'/subscriptions/{sub}'
    url = (f'https://management.azure.com{scope_id}'
           f'/providers/Microsoft.Security/alerts?api-version=2022-01-01')
    try:
        data = _get(url, token)
        items = []
        for a in data.get('value', []):
            props = a.get('properties', {})
            sev = props.get('severity')
            if severity and sev != severity:
                continue
            items.append({
                'name': a.get('name'),
                'alert_display_name': props.get('alertDisplayName'),
                'severity': sev,
                'status': props.get('status'),
                'description': (props.get('description') or '')[:200],
                'resource_identifiers': props.get('resourceIdentifiers'),
                'start_time': props.get('startTimeUtc'),
                'remediation': (props.get('remediationSteps') or '')[:200],
            })
            if len(items) >= top:
                break
        return {'ok': True, 'scope': scope_id, 'count': len(items), 'alerts': items}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "security.list_alerts", "doc": "[READ] Elenca gli alert di sicurezza attivi di Microsoft Defender for Cloud (severità, stato, risorsa). Args: {\"scope\": str (opz, default subscription), \"severity\": \"High\"|\"Medium\"|\"Low\" (opz), \"top\": int (opz, default 50)}. Ritorna alert di sicurezza.", "write": False, "entrypoint": "run"}
    ]
}
