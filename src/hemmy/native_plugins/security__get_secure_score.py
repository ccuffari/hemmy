# Plugin auto-generato per il tool 'security.get_secure_score'.
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
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    scope_id = scope or f'/subscriptions/{sub}'
    url = (f'https://management.azure.com{scope_id}'
           f'/providers/Microsoft.Security/secureScores?api-version=2020-01-01')
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    scores = []
    for s in data.get('value', []):
        props = s.get('properties', {})
        scores.append({
            'name': s.get('name'),
            'current': props.get('score', {}).get('current'),
            'max': props.get('score', {}).get('max'),
            'percentage': props.get('score', {}).get('percentage'),
            'weight': props.get('weight'),
        })
    return {'ok': True, 'scope': scope_id, 'count': len(scores), 'scores': scores}

MANIFEST = {
    "tools": [
        {"name": "security.get_secure_score", "doc": "[READ] Legge il Microsoft Defender for Cloud Secure Score della subscription (o di un RG). Args: {\"scope\": str (opz, default subscription)}. Ritorna punteggio corrente, max, percentuale e controlli.", "write": False, "entrypoint": "run"}
    ]
}
