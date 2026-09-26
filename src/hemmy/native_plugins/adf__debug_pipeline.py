# Plugin auto-generato per il tool 'adf.debug_pipeline'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import time
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def run(**kwargs):
    pipeline_name = kwargs.get('pipeline_name')
    if not pipeline_name:
        return {'ok': False, 'error': 'parametro pipeline_name obbligatorio'}
    parameters = kwargs.get('parameters') or {}
    timeout = int(kwargs.get('timeout_seconds', 300))
    sub = get_subscription_id()
    rg = os.environ.get('ADF_RESOURCE_GROUP')
    factory = os.environ.get('ADF_FACTORY_NAME')
    if not all([sub, rg, factory]):
        return {'ok': False, 'error': 'ADF_RESOURCE_GROUP/ADF_FACTORY_NAME non impostati'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    base = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}'
            f'/providers/Microsoft.DataFactory/factories/{factory}')

    url = f'{base}/createRun?api-version=2018-06-01'
    body = json.dumps({'pipelineName': pipeline_name, 'parameters': parameters}).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            created = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    run_id = created.get('runId')
    if not run_id:
        return {'ok': False, 'error': 'runId non ritornato', 'raw': created}

    deadline = time.time() + timeout
    status = 'InProgress'
    last = {}
    while time.time() < deadline:
        qurl = f'{base}/pipelineruns/{run_id}?api-version=2018-06-01'
        qreq = urllib.request.Request(qurl, method='GET')
        qreq.add_header('Authorization', 'Bearer ' + token)
        try:
            with urllib.request.urlopen(qreq, timeout=30) as resp:
                last = json.loads(resp.read().decode())
        except Exception as e:
            return {'ok': False, 'error': str(e), 'run_id': run_id}
        status = last.get('status')
        if status in ('Succeeded', 'Failed', 'Cancelled'):
            break
        time.sleep(5)

    return {
        'ok': status == 'Succeeded',
        'run_id': run_id,
        'pipeline_name': pipeline_name,
        'status': status,
        'start': last.get('runStart'),
        'end': last.get('runEnd'),
        'duration_ms': last.get('durationInMs'),
        'message': last.get('message'),
    }

MANIFEST = {
    "tools": [
        {"name": "adf.debug_pipeline", "doc": "[WRITE] Esegue una pipeline ADF in DEBUG mode (senza trigger, con parametri opzionali) e attende l'esito. Args: {\"pipeline_name\": str, \"parameters\": {} (opz), \"timeout_seconds\": int (opz, default 300)}. Ritorna run_id, stato, durata, errore.", "write": True, "entrypoint": "run"}
    ]
}
