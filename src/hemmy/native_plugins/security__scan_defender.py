# Plugin auto-generato per il tool 'security.scan_defender'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def run(**kwargs):
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}'
           f'/providers/Microsoft.Security/pricings?api-version=2023-01-01')
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    plans = []
    for p in data.get('value', []):
        props = p.get('properties', {})
        plans.append({
            'name': p.get('name'),
            'pricing_tier': props.get('pricingTier'),
            'subplan': props.get('subPlan'),
            'enabled': props.get('pricingTier') == 'Standard',
        })
    enabled = [p['name'] for p in plans if p['enabled']]
    disabled = [p['name'] for p in plans if not p['enabled']]
    return {'ok': True, 'count': len(plans), 'enabled': enabled, 'disabled': disabled, 'plans': plans}

MANIFEST = {
    "tools": [
        {"name": "security.scan_defender", "doc": "[READ] Legge lo stato dei piani Microsoft Defender for Cloud (pricing) sulla subscription: quali piani sono attivi (Servers, SQL, Storage, Containers, ecc.). Args: {}. Ritorna piani e tier.", "write": False, "entrypoint": "run"}
    ]
}
