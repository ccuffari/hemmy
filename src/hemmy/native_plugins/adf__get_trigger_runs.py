# Plugin auto-generato per il tool 'adf.get_trigger_runs'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def run(**kwargs):
    last_days = int(kwargs.get('last_days', 7))
    trigger_name = kwargs.get('trigger_name')
    sub = get_subscription_id()
    rg = os.environ.get('ADF_RESOURCE_GROUP')
    factory = os.environ.get('ADF_FACTORY_NAME')
    if not all([sub, rg, factory]):
        return {'ok': False, 'error': 'ADF_RESOURCE_GROUP/ADF_FACTORY_NAME non impostati'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    start = (datetime.now(timezone.utc) - timedelta(days=last_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    end = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}'
           f'/providers/Microsoft.DataFactory/factories/{factory}'
           f'/queryTriggerRuns?api-version=2018-06-01')
    body = json.dumps({
        'lastUpdatedAfter': start,
        'lastUpdatedBefore': end,
    }).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    runs = []
    for r in data.get('value', []):
        if trigger_name and r.get('triggerName') != trigger_name:
            continue
        runs.append({
            'trigger_name': r.get('triggerName'),
            'run_id': r.get('triggerRunId'),
            'status': r.get('status'),
            'start': r.get('triggerRunTimestamp'),
            'message': r.get('message'),
        })
    return {'ok': True, 'count': len(runs), 'runs': runs}

MANIFEST = {
    "tools": [
        {"name": "adf.get_trigger_runs", "doc": "[READ] Elenca le esecuzioni recenti dei trigger ADF (trigger runs) con stato. Args: {\"last_days\": int (opz, default 7), \"trigger_name\": str (opz)}. Ritorna trigger, stato, orari, errore.", "write": False, "entrypoint": "run"}
    ]
}
