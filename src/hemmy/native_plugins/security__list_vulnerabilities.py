# Plugin auto-generato per il tool 'security.list_vulnerabilities'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
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
           f'/providers/Microsoft.Security/assessments?api-version=2021-06-01')
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    items = []
    for a in data.get('value', []):
        props = a.get('properties', {})
        status = props.get('status', {})
        meta = props.get('metadata', {})
        sev = meta.get('severity')
        if severity and sev != severity:
            continue
        items.append({
            'name': a.get('name'),
            'display_name': meta.get('displayName'),
            'severity': sev,
            'status_code': status.get('code'),
            'description': (meta.get('description') or '')[:200],
            'remediation': (meta.get('remediationDescription') or '')[:200],
        })
        if len(items) >= top:
            break
    return {'ok': True, 'scope': scope_id, 'count': len(items), 'assessments': items}

MANIFEST = {
    "tools": [
        {"name": "security.list_vulnerabilities", "doc": "[READ] Elenca le raccomandazioni/vulnerabilità di Microsoft Defender for Cloud (assessments) per la subscription o un RG. Args: {\"scope\": str (opz, default subscription), \"severity\": \"High\"|\"Medium\"|\"Low\" (opz), \"top\": int (opz, default 50)}. Ritorna raccomandazioni con severità e stato.", "write": False, "entrypoint": "run"}
    ]
}
