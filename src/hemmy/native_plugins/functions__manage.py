# Plugin auto-generato per il tool 'functions.manage'.
# Installato via meta.install_tool con approvazione umana.

import os, json, urllib.request, urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _tok():
    # Choke-point condiviso: sessione OAuth dell'utente corrente prima
    # di tutto, DefaultAzureCredential solo per CLI locale (vedi
    # utils/azure_auth.get_arm_token). PRIMA questa funzione bypassava
    # get_arm_token con una propria DefaultAzureCredential diretta,
    # condivisa tra tutti gli utenti del processo.
    try:
        return get_arm_token()
    except Exception:
        return None


def _req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Authorization', 'Bearer ' + token)
    r.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            txt = x.read().decode()
            return {'ok': True, 'status': x.status, 'data': json.loads(txt) if txt else {}}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'status': e.code, 'error': e.read().decode()[:500]}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def run(**kwargs):
    action = kwargs.get('action')
    p = kwargs.get('params') or {}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _tok()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}
    rg = p.get('resource_group')
    app = p.get('app_name')
    base = f'https://management.azure.com/subscriptions/{sub}' + (f'/resourceGroups/{rg}' if rg else '')
    if action == 'list_function_apps':
        r = _req('GET', f'{base}/providers/Microsoft.Web/sites?api-version=2022-09-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            if x.get('kind', '').lower().find('functionapp') == -1 and 'function' not in x.get('kind', '').lower():
                continue
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'state': pr.get('state'), 'location': x.get('location'), 'kind': x.get('kind'), 'default_host_name': pr.get('defaultHostName'), 'runtime': pr.get('siteConfig', {}).get('linuxFxVersion') or pr.get('siteConfig', {}).get('netFrameworkVersion'), 'https_only': pr.get('httpsOnly')})
        return {'ok': True, 'action': action, 'count': len(out), 'function_apps': out}
    if action == 'get_function_app':
        if not rg or not app:
            return {'ok': False, 'error': 'resource_group e app_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Web/sites/{app}?api-version=2022-09-01', token)
        if not r.get('ok'):
            return r
        pr = r['data'].get('properties', {})
        return {'ok': True, 'action': action, 'name': app, 'state': pr.get('state'), 'default_host_name': pr.get('defaultHostName'), 'kind': r['data'].get('kind'), 'https_only': pr.get('httpsOnly'), 'enabled': pr.get('enabled'), 'site_config': pr.get('siteConfig')}
    if action == 'list_functions':
        if not rg or not app:
            return {'ok': False, 'error': 'resource_group e app_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Web/sites/{app}/functions?api-version=2022-09-01', token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'language': pr.get('language'), 'is_disabled': pr.get('isDisabled'), 'invoke_url_template': pr.get('invokeUrlTemplate'), 'script_href': pr.get('scriptHref')})
        return {'ok': True, 'action': action, 'count': len(out), 'functions': out}
    if action == 'list_function_keys':
        if not rg or not app:
            return {'ok': False, 'error': 'resource_group e app_name richiesti'}
        fn = p.get('function_name')
        url = (f'{base}/providers/Microsoft.Web/sites/{app}/functions/{fn}/listKeys?api-version=2022-09-01'
               if fn else f'{base}/providers/Microsoft.Web/sites/{app}/host/default/listKeys?api-version=2022-09-01')
        r = _req('POST', url, token, {})
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'keys': r['data']}
    if action == 'get_app_settings':
        if not rg or not app:
            return {'ok': False, 'error': 'resource_group e app_name richiesti'}
        r = _req('POST', f'{base}/providers/Microsoft.Web/sites/{app}/config/appsettings/list?api-version=2022-09-01', token, {})
        if not r.get('ok'):
            return r
        props = r['data'].get('properties', {})
        safe = {k: ('***' if any(s in k.lower() for s in ('secret', 'password', 'key', 'token', 'conn')) else v) for k, v in props.items()}
        return {'ok': True, 'action': action, 'count': len(safe), 'settings': safe}
    if action == 'list_deployments':
        if not rg or not app:
            return {'ok': False, 'error': 'resource_group e app_name richiesti'}
        r = _req('GET', f'{base}/providers/Microsoft.Web/sites/{app}/deployments?api-version=2022-09-01', token)
        if not r.get('ok'):
            return r
        out = [{'id': x.get('id'), 'status': x.get('properties', {}).get('status'), 'author': x.get('properties', {}).get('author'), 'message': x.get('properties', {}).get('message'), 'start_time': x.get('properties', {}).get('startTime'), 'end_time': x.get('properties', {}).get('endTime')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'deployments': out}
    if action == 'get_functions_summary':
        r = _req('GET', f'{base}/providers/Microsoft.Web/sites?api-version=2022-09-01', token)
        if not r.get('ok'):
            return r
        apps = [x for x in r['data'].get('value', []) if 'function' in x.get('kind', '').lower()]
        running = sum(1 for x in apps if x.get('properties', {}).get('state') == 'Running')
        return {'ok': True, 'action': action, 'total': len(apps), 'running': running, 'stopped': len(apps) - running}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "functions.manage", "doc": "[WRITE] Gestione Azure Functions: lista function app, funzioni, run history, chiavi, configurazione, stato. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_function_apps, get_function_app, list_functions, list_function_keys, get_app_settings, list_deployments, get_functions_summary.", "write": True, "entrypoint": "run"}
    ]
}
