# Plugin auto-generato per il tool 'adf.cancel_run'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def run(**kwargs):
    run_id = kwargs.get('run_id')
    if not run_id:
        return {'ok': False, 'error': 'parametro run_id obbligatorio'}
    sub = get_subscription_id()
    rg = os.environ.get('ADF_RESOURCE_GROUP')
    factory = os.environ.get('ADF_FACTORY_NAME')
    if not all([sub, rg, factory]):
        return {'ok': False, 'error': 'ADF_RESOURCE_GROUP/ADF_FACTORY_NAME non impostati'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}'
           f'/providers/Microsoft.DataFactory/factories/{factory}'
           f'/pipelineruns/{run_id}/cancel?api-version=2018-06-01')
    req = urllib.request.Request(url, data=b'', method='POST')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        return {'ok': True, 'run_id': run_id, 'cancelled': True}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "adf.cancel_run", "doc": "[WRITE] Cancella una run di pipeline ADF in corso. Args: {\"run_id\": str}. Ritorna l'esito della cancellazione.", "write": True, "entrypoint": "run"}
    ]
}
