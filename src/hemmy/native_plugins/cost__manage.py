# Plugin auto-generato per il tool 'cost.manage'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _token():
    try:
        return get_arm_token(), None
    except Exception as e:
        return None, {'stage': 'token', 'error': str(e)}
def _api(method, path, body=None, token=None):
    url = 'https://management.azure.com' + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return {'status': resp.status, 'body': json.loads(raw) if raw else {}}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return {'status': e.code, 'error': parsed}
    except Exception as e:
        return {'status': 0, 'error': str(e)}


def _sub():
    return get_subscription_id()
def _month_period():
    now = datetime.utcnow()
    start = now.replace(day=1).strftime('%Y-%m-%dT00:00:00Z')
    end = now.strftime('%Y-%m-%dT00:00:00Z')
    return {'from': start, 'to': end}


def run(**kwargs):
    action = kwargs.get('action')
    params = kwargs.get('params') or {}
    if not action:
        return {'ok': False, 'error': 'action mancante'}
    token, err = _token()
    if err:
        return {'ok': False, 'error': err}
    try:
        sub = _sub()
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    scope = f'/subscriptions/{sub}'
    api = '2023-11-01'

    if action == 'get_current_cost':
        body = {
            'type': 'ActualCost',
            'timeframe': 'Custom',
            'timePeriod': _month_period(),
            'dataset': {
                'granularity': 'None',
                'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
            },
        }
        r = _api('POST', f'{scope}/providers/Microsoft.CostManagement/query?api-version={api}', body, token)

    elif action == 'get_forecast':
        start = datetime.utcnow().strftime('%Y-%m-%dT00:00:00Z')
        end = (datetime.utcnow() + timedelta(days=30)).strftime('%Y-%m-%dT00:00:00Z')
        body = {
            'type': 'Usage',
            'timeframe': 'Custom',
            'timePeriod': {'from': start, 'to': end},
            'dataset': {
                'granularity': 'Daily',
                'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
            },
        }
        r = _api('POST', f'{scope}/providers/Microsoft.CostManagement/forecast?api-version={api}', body, token)

    elif action == 'get_cost_by_service':
        body = {
            'type': 'ActualCost',
            'timeframe': 'Custom',
            'timePeriod': _month_period(),
            'dataset': {
                'granularity': 'None',
                'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
                'grouping': [{'type': 'Dimension', 'name': 'ServiceName'}],
            },
        }
        r = _api('POST', f'{scope}/providers/Microsoft.CostManagement/query?api-version={api}', body, token)

    elif action == 'get_cost_by_resource_group':
        body = {
            'type': 'ActualCost',
            'timeframe': 'Custom',
            'timePeriod': _month_period(),
            'dataset': {
                'granularity': 'None',
                'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
                'grouping': [{'type': 'Dimension', 'name': 'ResourceGroupName'}],
            },
        }
        r = _api('POST', f'{scope}/providers/Microsoft.CostManagement/query?api-version={api}', body, token)

    elif action == 'get_daily_costs':
        days = int(params.get('days', 30))
        start = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00Z')
        end = datetime.utcnow().strftime('%Y-%m-%dT00:00:00Z')
        body = {
            'type': 'ActualCost',
            'timeframe': 'Custom',
            'timePeriod': {'from': start, 'to': end},
            'dataset': {
                'granularity': 'Daily',
                'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
            },
        }
        r = _api('POST', f'{scope}/providers/Microsoft.CostManagement/query?api-version={api}', body, token)

    elif action == 'list_budgets':
        r = _api('GET', f'{scope}/providers/Microsoft.Consumption/budgets?api-version={api}', None, token)

    elif action == 'create_budget':
        name = params.get('name', 'budget-monthly')
        amount = params.get('amount', 100)
        emails = params.get('emails', [])
        notifications = {}
        for thr in params.get('thresholds', [50, 80, 100]):
            notifications[f'Actual_{thr}'] = {
                'enabled': True,
                'operator': 'GreaterThan',
                'threshold': thr,
                'contactEmails': emails,
                'thresholdType': 'Actual',
            }
        body = {
            'properties': {
                'category': 'Cost',
                'amount': amount,
                'timeGrain': 'Monthly',
                'timePeriod': {
                    'startDate': datetime.utcnow().strftime('%Y-%m-01T00:00:00Z'),
                    'endDate': (datetime.utcnow() + timedelta(days=3650)).strftime('%Y-%m-%dT00:00:00Z'),
                },
                'notifications': notifications,
            },
        }
        r = _api('PUT', f'{scope}/providers/Microsoft.Consumption/budgets/{name}?api-version={api}', body, token)

    elif action == 'list_action_groups':
        rg = params.get('resource_group')
        r = _api('GET', f'{scope}/resourceGroups/{rg}/providers/Microsoft.Insights/actionGroups?api-version=2023-01-01', None, token)

    elif action == 'create_action_group':
        rg = params.get('resource_group')
        name = params.get('name', 'ag-cost-alerts')
        emails = params.get('emails', [])
        body = {
            'location': 'global',
            'properties': {
                'groupShortName': params.get('short_name', 'costalert'),
                'enabled': True,
                'emailReceivers': [
                    {'name': f'email{i}', 'emailAddress': e, 'useCommonAlertSchema': True}
                    for i, e in enumerate(emails)
                ],
            },
        }
        r = _api('PUT', f'{scope}/resourceGroups/{rg}/providers/Microsoft.Insights/actionGroups/{name}?api-version=2023-01-01', body, token)

    else:
        return {'ok': False, 'error': 'azione non supportata: ' + str(action)}

    if 'error' in r:
        return {'ok': False, 'action': action, 'status': r['status'], 'error': r['error']}
    return {'ok': True, 'action': action, 'status': r['status'], 'result': r['body']}

MANIFEST = {
    "tools": [
        {"name": "cost.manage", "doc": "[WRITE] Monitoraggio costi Azure (Cost Management API + Budget). Azioni: get_current_cost, get_forecast, get_cost_by_service, get_cost_by_resource_group, get_daily_costs, list_budgets, create_budget, list_action_groups, create_action_group. Usa le credenziali Azure del SP (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {} (opz)}.", "write": True, "entrypoint": "run"}
    ]
}
