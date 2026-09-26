# Plugin auto-generato per il tool 'policy.manage'.
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
    scope = p.get('scope') or (f'/subscriptions/{sub}/resourceGroups/{rg}' if rg else f'/subscriptions/{sub}')
    if action == 'list_assignments':
        url = f'https://management.azure.com{scope}/providers/Microsoft.Authorization/policyAssignments?api-version=2022-06-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = []
        for x in r['data'].get('value', []):
            pr = x.get('properties', {})
            out.append({'name': x.get('name'), 'display_name': pr.get('displayName'), 'policy_definition_id': pr.get('policyDefinitionId'), 'scope': pr.get('scope'), 'enforcement_mode': pr.get('enforcementMode'), 'description': pr.get('description')})
        return {'ok': True, 'action': action, 'scope': scope, 'count': len(out), 'assignments': out}
    if action == 'get_assignment':
        name = p.get('assignment_name')
        if not name:
            return {'ok': False, 'error': 'assignment_name richiesto'}
        url = f'https://management.azure.com{scope}/providers/Microsoft.Authorization/policyAssignments/{name}?api-version=2022-06-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        return {'ok': True, 'action': action, 'assignment': r['data']}
    if action == 'list_compliance':
        url = f'https://management.azure.com{scope}/providers/Microsoft.PolicyInsights/policyStates/latest/summarize?api-version=2019-10-01'
        r = _req('POST', url, token, {})
        if not r.get('ok'):
            return r
        vals = r['data'].get('value', [])
        out = []
        for v in vals:
            s = v.get('results', {}).get('nonCompliantResources', 0)
            t = v.get('results', {}).get('resourceDetails', [])
            out.append({'policy_assignment': v.get('policyAssignmentId'), 'policy_definition': v.get('policyDefinitionId'), 'non_compliant_resources': s, 'policy_details': t})
        return {'ok': True, 'action': action, 'scope': scope, 'count': len(out), 'compliance': out}
    if action == 'list_definitions':
        url = f'https://management.azure.com/subscriptions/{sub}/providers/Microsoft.Authorization/policyDefinitions?api-version=2021-06-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'display_name': x.get('properties', {}).get('displayName'), 'policy_type': x.get('properties', {}).get('policyType'), 'mode': x.get('properties', {}).get('mode')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'definitions': out}
    if action == 'list_initiatives':
        url = f'https://management.azure.com/subscriptions/{sub}/providers/Microsoft.Authorization/policySetDefinitions?api-version=2021-06-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'display_name': x.get('properties', {}).get('displayName'), 'policy_type': x.get('properties', {}).get('policyType'), 'policy_count': len(x.get('properties', {}).get('policyDefinitions', []))} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'initiatives': out}
    if action == 'list_remediations':
        url = f'https://management.azure.com{scope}/providers/Microsoft.PolicyInsights/remediations?api-version=2021-10-01'
        r = _req('GET', url, token)
        if not r.get('ok'):
            return r
        out = [{'name': x.get('name'), 'policy_assignment': x.get('properties', {}).get('policyAssignmentId'), 'provisioning_state': x.get('properties', {}).get('provisioningState'), 'created': x.get('properties', {}).get('createdOn')} for x in r['data'].get('value', [])]
        return {'ok': True, 'action': action, 'count': len(out), 'remediations': out}
    if action == 'get_policy_summary':
        url = f'https://management.azure.com{scope}/providers/Microsoft.PolicyInsights/policyStates/latest/summarize?api-version=2019-10-01'
        r = _req('POST', url, token, {})
        if not r.get('ok'):
            return r
        vals = r['data'].get('value', [])
        total_nc = 0
        for v in vals:
            total_nc += v.get('results', {}).get('nonCompliantResources', 0)
        return {'ok': True, 'action': action, 'scope': scope, 'assignments_with_issues': len(vals), 'total_non_compliant_resources': total_nc}
    return {'ok': False, 'error': f'azione non supportata: {action}'}

MANIFEST = {
    "tools": [
        {"name": "policy.manage", "doc": "[WRITE] Gestione Azure Policy: lista assegnazioni, stato compliance, definizioni, iniziative, remediation. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": str, \"params\": {}}. Azioni: list_assignments, get_assignment, list_compliance, list_definitions, list_initiatives, list_remediations, get_policy_summary.", "write": True, "entrypoint": "run"}
    ]
}
