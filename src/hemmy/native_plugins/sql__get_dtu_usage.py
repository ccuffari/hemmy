# Plugin auto-generato per il tool 'sql.get_dtu_usage'.
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
    resource_group = kwargs.get('resource_group') or os.environ.get('SQL_RESOURCE_GROUP')
    server_name = kwargs.get('server_name') or os.environ.get('SQL_SERVER_NAME')
    database_name = kwargs.get('database_name') or os.environ.get('SQL_DATABASE_NAME')
    timespan = kwargs.get('timespan', 'PT1H')
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    if not resource_group or not server_name or not database_name:
        return {'ok': False, 'error': 'resource_group, server_name, database_name obbligatori'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    res_id = (f'/subscriptions/{sub}/resourceGroups/{resource_group}'
              f'/providers/Microsoft.Sql/servers/{server_name}/databases/{database_name}')
    metrics = 'dtu_consumption_percent,cpu_percent,storage_percent,physical_data_read_percent,log_write_percent'
    url = (f'https://management.azure.com{res_id}/providers/microsoft.insights/metrics'
           f'?api-version=2018-01-01&metricnames={metrics}&timespan={timespan}&interval=PT5M&aggregation=Average,Maximum')
    try:
        data = _get(url, token)
        out = []
        for m in data.get('value', []):
            series = []
            for ts in m.get('timeseries', []):
                for pt in ts.get('data', []):
                    series.append({'time': pt.get('timeStamp'),
                                   'avg': pt.get('average'), 'max': pt.get('maximum')})
            out.append({'metric': m.get('name', {}).get('value'), 'unit': m.get('unit'), 'series': series})
        return {'ok': True, 'database': database_name, 'timespan': timespan, 'metrics': out}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_dtu_usage", "doc": "[READ] Legge il consumo DTU/vCore di un Azure SQL Database via ARM Metrics API (dtu_consumption_percent, cpu_percent, storage_percent). Args: {\"resource_group\": str (opz), \"server_name\": str (opz), \"database_name\": str (opz), \"timespan\": str (opz, default 'PT1H')}. Ritorna serie temporali di consumo.", "write": False, "entrypoint": "run"}
    ]
}
