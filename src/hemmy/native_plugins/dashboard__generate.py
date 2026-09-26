# Plugin auto-generato per il tool 'dashboard.generate'.
# Installato via meta.install_tool con approvazione umana.

import os, json, base64, datetime, importlib.util, urllib.request, urllib.error
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


def _get(u, t):
    r = urllib.request.Request(u, method='GET')
    r.add_header('Authorization', 'Bearer ' + t)
    with urllib.request.urlopen(r, timeout=60) as x:
        return json.loads(x.read().decode())


def _post(u, t, b):
    r = urllib.request.Request(u, data=json.dumps(b).encode(), method='POST')
    r.add_header('Authorization', 'Bearer ' + t)
    r.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(r, timeout=60) as x:
        return json.loads(x.read().decode())


def _disc(rg, t, s):
    u = (f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/resources?api-version=2021-04-01'
         if rg else f'https://management.azure.com/subscriptions/{s}/resources?api-version=2021-04-01')
    o = []
    while u:
        d = _get(u, t)
        o += d.get('value', [])
        u = d.get('nextLink')
    return o


def _h(iss):
    if not iss:
        return 'ok'
    for i in iss:
        if any(k in str(i).lower() for k in ('error', 'fail', 'down', 'offline', 'not ready', 'unavailable')):
            return 'error'
    return 'warn'


def _met(u, t, names):
    o = {}
    try:
        d = _get(f'{u}/providers/microsoft.insights/metrics?api-version=2018-01-01&metricnames={names}&timespan=PT1H&interval=PT5M&aggregation=Average,Maximum,Total', t)
        for m in d.get('value', []):
            n = m.get('name', {}).get('value')
            ser = m.get('timeseries', [{}])[0].get('data', [])
            for a in ('maximum', 'average', 'total'):
                v = [p.get(a) for p in ser if p.get(a) is not None]
                if v:
                    o[f'{n}_{a}'] = round(max(v) if a == 'maximum' else (sum(v) if a == 'total' else v[-1]), 2)
    except Exception:
        pass
    return o


def _sqls(res, t, s):
    i, m = [], {}
    rg, sv = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{sv}'
    try:
        p = _get(f'{b}?api-version=2021-11-01', t).get('properties', {})
        m.update(state=p.get('state'), fqdn=p.get('fullyQualifiedDomainName'), version=p.get('version'), public_access=p.get('publicNetworkAccess'), admin_login=p.get('administratorLogin'), min_tls=p.get('minimalTlsVersion'))
        if p.get('state') != 'Ready':
            i.append(f"Server non Ready: {p.get('state')}")
        if p.get('publicNetworkAccess') == 'Enabled':
            i.append('Public network access abilitato')
        fw = _get(f'{b}/firewallRules?api-version=2021-11-01', t).get('value', [])
        m['firewall_ip_rules'] = len(fw)
        m['firewall_ip_list'] = ', '.join(f"{r.get('properties',{}).get('startIpAddress')}-{r.get('properties',{}).get('endIpAddress')}" for r in fw) or 'nessuna'
        m['allow_azure_services'] = any(r.get('properties', {}).get('startIpAddress') == '0.0.0.0' for r in fw)
        vn = _get(f'{b}/virtualNetworkRules?api-version=2021-11-01', t).get('value', [])
        m['firewall_vnet_rules'] = len(vn)
        if not fw and not vn:
            i.append('Nessuna regola firewall')
    except Exception as e:
        i.append(f'check SQL fallito: {e}')
    return i, m


def _sqld(res, t, s):
    i, m = [], {}
    p = res['id'].split('/')
    rg, sv, db = p[4], p[8], p[10]
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{sv}/databases/{db}'
    try:
        d = _get(f'{b}?api-version=2021-11-01', t)
        pr = d.get('properties', {})
        m.update(status=pr.get('status'), sku=d.get('sku', {}).get('name'), tier=d.get('sku', {}).get('tier'), capacity=d.get('sku', {}).get('capacity'), collation=pr.get('collation'), max_size_gb=round(pr.get('maxSizeBytes', 0) / (1024**3), 1) if pr.get('maxSizeBytes') else None, zone_redundant=pr.get('zoneRedundant'))
        if pr.get('status') != 'Online':
            i.append(f"DB non Online: {pr.get('status')}")
        m.update(_met(f'https://management.azure.com{res["id"]}', t, 'dtu_consumption_percent,cpu_percent,storage_percent,connection_successful,connection_failed'))
        if m.get('dtu_consumption_percent_max', 0) > 80:
            i.append(f"DTU {m['dtu_consumption_percent_max']}% > 80%")
    except Exception as e:
        i.append(f'check DB fallito: {e}')
    return i, m


def _st(res, t, s):
    i, m = [], {}
    rg, ac = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Storage/storageAccounts/{ac}'
    try:
        x = _get(f'{b}?api-version=2023-01-01', t)
        p = x.get('properties', {})
        nr = p.get('networkAcls', {})
        m.update(sku=x.get('sku', {}).get('name'), kind=x.get('kind'), location=x.get('location'), provisioning=p.get('provisioningState'), https_only=p.get('supportsHttpsTrafficOnly'), min_tls=p.get('minimumTlsVersion'), access_tier=p.get('accessTier'), blob_endpoint=p.get('primaryEndpoints', {}).get('blob'), network_default=nr.get('defaultAction'), network_bypass=','.join(nr.get('bypass', [])), public_access=p.get('publicNetworkAccess'), allow_blob_public=p.get('allowBlobPublicAccess'))
        if p.get('provisioningState') != 'Succeeded':
            i.append(f"Provisioning: {p.get('provisioningState')}")
        if p.get('minimumTlsVersion') not in ('TLS1_2', 'TLS1_3'):
            i.append(f"TLS debole: {p.get('minimumTlsVersion')}")
        if nr.get('defaultAction') == 'Allow':
            i.append('Network default action = Allow')
        if p.get('allowBlobPublicAccess'):
            i.append('Blob public access abilitato')
        m.update(_met(f'https://management.azure.com{res["id"]}', t, 'UsedCapacity,Availability,Transactions,Ingress,Egress'))
        if m.get('Availability_average') is not None and m['Availability_average'] < 99:
            i.append(f"Availability {m['Availability_average']}% < 99%")
        try:
            lc = _get(f'{b}/managementPolicies/default?api-version=2023-01-01', t)
            rl = lc.get('properties', {}).get('policy', {}).get('rules', [])
            m['lifecycle_rules'] = len(rl)
            m['lifecycle_rule_names'] = ', '.join(r.get('name', '') for r in rl) or 'nessuna'
        except urllib.error.HTTPError as e:
            if e.code == 404:
                m['lifecycle_rules'] = 0
                i.append('Nessuna lifecycle policy')
    except Exception as e:
        i.append(f'check Storage fallito: {e}')
    return i, m


def _adf(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.DataFactory/factories/{nm}'
    try:
        f = _get(f'{b}?api-version=2018-06-01', t)
        p = f.get('properties', {})
        m.update(provisioning=p.get('provisioningState'), public_access=p.get('publicNetworkAccess'), location=f.get('location'), version=p.get('version'))
        if p.get('provisioningState') != 'Succeeded':
            i.append(f"Provisioning: {p.get('provisioningState')}")
        if p.get('publicNetworkAccess') == 'Enabled':
            i.append('Public network access abilitato')
        try:
            st = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
            en = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            rd = _post(f'{b}/queryPipelineRuns?api-version=2018-06-01', t, {'lastUpdatedAfter': st, 'lastUpdatedBefore': en})
            rn = rd.get('value', [])
            m['pipeline_runs_7d'] = len(rn)
            fl = [r for r in rn if r.get('status') == 'Failed']
            m['pipeline_failed_7d'] = len(fl)
            if fl:
                i.append(f'{len(fl)} pipeline fallite negli ultimi 7 giorni')
        except Exception:
            pass
        for k, pth in (('pipelines_count', 'pipelines'), ('linked_services_count', 'linkedservices'), ('datasets_count', 'datasets')):
            try:
                m[k] = len(_get(f'{b}/{pth}?api-version=2018-06-01', t).get('value', []))
            except Exception:
                pass
    except Exception as e:
        i.append(f'check ADF fallito: {e}')
    return i, m


def _vnet(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Network/virtualNetworks/{nm}'
    try:
        p = _get(f'{b}?api-version=2023-09-01', t).get('properties', {})
        sn = p.get('subnets', [])
        m.update(address_space=','.join(p.get('addressSpace', {}).get('addressPrefixes', [])), subnets=len(sn), subnet_names=', '.join(x.get('name', '') for x in sn), provisioning=p.get('provisioningState'), dns_servers=','.join(p.get('dhcpOptions', {}).get('dnsServers', [])) or 'default Azure')
        if p.get('provisioningState') != 'Succeeded':
            i.append(f"Provisioning: {p.get('provisioningState')}")
        try:
            pr = _get(f'{b}/virtualNetworkPeerings?api-version=2023-09-01', t).get('value', [])
            m['peerings'] = len(pr)
            for pe in pr:
                stt = pe.get('properties', {}).get('peeringState')
                if stt != 'Connected':
                    i.append(f"Peering {pe.get('name')}: {stt}")
        except Exception:
            pass
    except Exception as e:
        i.append(f'check VNet fallito: {e}')
    return i, m


def _dbw(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Databricks/workspaces/{nm}'
    try:
        w = _get(f'{b}?api-version=2023-02-01', t)
        p = w.get('properties', {})
        m.update(provisioning=p.get('provisioningState'), sku=w.get('sku', {}).get('name'), workspace_url=p.get('workspaceUrl'), managed_rg=p.get('managedResourceGroupId', '').split('/')[-1], location=w.get('location'))
        if p.get('provisioningState') != 'Succeeded':
            i.append(f"Provisioning: {p.get('provisioningState')}")
    except Exception as e:
        i.append(f'check Databricks fallito: {e}')
    return i, m


def _la(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.OperationalInsights/workspaces/{nm}'
    try:
        p = _get(f'{b}?api-version=2022-10-01', t).get('properties', {})
        m.update(sku=p.get('sku', {}).get('name'), retention_days=p.get('retentionInDays'), provisioning=p.get('provisioningState'), daily_quota_gb=p.get('workspaceCapping', {}).get('dailyQuotaGb'), customer_id=p.get('customerId'))
        if p.get('provisioningState') != 'Succeeded':
            i.append(f"Provisioning: {p.get('provisioningState')}")
    except Exception as e:
        i.append(f'check LogAnalytics fallito: {e}')
    return i, m


def _kv(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.KeyVault/vaults/{nm}'
    try:
        p = _get(f'{b}?api-version=2023-07-01', t).get('properties', {})
        na = p.get('networkAcls', {})
        m.update(sku=p.get('sku', {}).get('name'), rbac_authorization=p.get('enableRbacAuthorization'), soft_delete=p.get('enableSoftDelete'), soft_delete_retention_days=p.get('softDeleteRetentionInDays'), purge_protection=p.get('enablePurgeProtection'), public_access=p.get('publicNetworkAccess'), vault_uri=p.get('vaultUri'), network_default=na.get('defaultAction'))
        if not p.get('enableSoftDelete'):
            i.append('Soft delete disabilitato')
        if not p.get('enablePurgeProtection'):
            i.append('Purge protection disabilitato')
        if na.get('defaultAction') == 'Allow':
            i.append('Network default action = Allow')
    except Exception as e:
        i.append(f'check KeyVault fallito: {e}')
    return i, m


def _pe(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Network/privateEndpoints/{nm}'
    try:
        p = _get(f'{b}?api-version=2023-09-01', t).get('properties', {})
        cn = p.get('privateLinkServiceConnections', [])
        st = cn[0].get('properties', {}).get('privateLinkServiceConnectionState', {}) if cn else {}
        m.update(status=st.get('status'), description=st.get('description'), target=p.get('privateLinkServiceId', '').split('/')[-1], subnet=p.get('subnet', {}).get('id', '').split('/')[-1])
        if st.get('status') != 'Approved':
            i.append(f"PE non Approved: {st.get('status')}")
    except Exception as e:
        i.append(f'check PE fallito: {e}')
    return i, m


def _nsg(res, t, s):
    i, m = [], {}
    rg, nm = res['id'].split('/')[4], res['name']
    b = f'https://management.azure.com/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.Network/networkSecurityGroups/{nm}'
    try:
        p = _get(f'{b}?api-version=2023-09-01', t).get('properties', {})
        rl = p.get('securityRules', [])
        m.update(custom_rules=len(rl), provisioning=p.get('provisioningState'))
        for r in rl:
            rp = r.get('properties', {})
            if rp.get('access') == 'Allow' and rp.get('sourceAddressPrefix') in ('*', '0.0.0.0/0', 'Internet'):
                i.append(f"Regola {r.get('name')} aperta a Internet")
    except Exception as e:
        i.append(f'check NSG fallito: {e}')
    return i, m


CK = {'Microsoft.Sql/servers': _sqls, 'Microsoft.Sql/servers/databases': _sqld, 'Microsoft.Storage/storageAccounts': _st, 'Microsoft.DataFactory/factories': _adf, 'Microsoft.Network/virtualNetworks': _vnet, 'Microsoft.Databricks/workspaces': _dbw, 'Microsoft.OperationalInsights/workspaces': _la, 'Microsoft.KeyVault/vaults': _kv, 'Microsoft.Network/privateEndpoints': _pe, 'Microsoft.Network/networkSecurityGroups': _nsg}


def _load_render():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, 'dashboard__render.py')
        if not os.path.exists(p):
            return None
        sp = importlib.util.spec_from_file_location('dash_render', p)
        md = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(md)
        return md.render_html
    except Exception:
        return None


def _load_diagram():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, 'diagram__drawio.py')
        if not os.path.exists(p):
            return None
        sp = importlib.util.spec_from_file_location('dd', p)
        md = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(md)
        return md.run
    except Exception:
        return None


def _load_cost():
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, 'cost__manage.py')
        if not os.path.exists(p):
            return None
        sp = importlib.util.spec_from_file_location('cm', p)
        md = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(md)
        return md.run
    except Exception:
        return None


def _rows(res):
    try:
        return res.get('result', {}).get('properties', {}).get('rows', [])
    except Exception:
        return []


def _collect_costs(rg, sub):
    fn = _load_cost()
    if not fn:
        return None
    out = {}
    try:
        r = fn(action='get_current_cost', params={'resource_group': rg} if rg else {})
        rows = _rows(r)
        if rows:
            out['current_cost'] = round(sum(x[0] for x in rows if isinstance(x[0], (int, float))), 2)
    except Exception:
        pass
    try:
        r = fn(action='get_forecast', params={'resource_group': rg} if rg else {})
        rows = _rows(r)
        if rows:
            out['forecast'] = round(sum(x[0] for x in rows if isinstance(x[0], (int, float))), 2)
            out['forecast_daily'] = [{'date': x[1], 'cost': round(x[0], 4)} for x in rows if len(x) > 1]
    except Exception:
        pass
    try:
        r = fn(action='get_daily_costs', params={'resource_group': rg} if rg else {})
        rows = _rows(r)
        vals = [x[0] for x in rows if isinstance(x[0], (int, float))]
        if vals:
            out['daily_cost'] = round(sum(vals) / len(vals), 2)
    except Exception:
        pass
    try:
        r = fn(action='get_cost_by_service', params={'resource_group': rg} if rg else {})
        rows = _rows(r)
        out['by_service'] = [{'service': x[1], 'cost': round(x[0], 4)} for x in rows if len(x) > 1]
    except Exception:
        pass
    try:
        r = fn(action='get_cost_by_resource_group', params={})
        rows = _rows(r)
        out['by_rg'] = [{'rg': x[1], 'cost': round(x[0], 4)} for x in rows if len(x) > 1]
    except Exception:
        pass
    return out or None


def run(**kwargs):
    rg = kwargs.get('resource_group')
    od = kwargs.get('output_dir', 'docs/dashboards')
    idg = kwargs.get('include_diagram', True)
    ic = kwargs.get('include_costs', True)
    s = get_subscription_id()
    if not s:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    t = _tok()
    if not t:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}
    sc = rg or f'subscription-{s[:8]}'
    try:
        rs = _disc(rg, t, s)
    except Exception as e:
        return {'ok': False, 'error': f'discovery fallita: {e}'}
    res = []
    for r in rs:
        ck = CK.get(r.get('type'))
        if not ck:
            continue
        try:
            i, m = ck(r, t, s)
        except Exception as e:
            i, m = [f'checker error: {e}'], {}
        res.append({'resource': r.get('name'), 'type': r.get('type'), 'rg': r['id'].split('/')[4], 'health': _h(i), 'metrics': m, 'issues': i})
    summary = {'ok': 0, 'warn': 0, 'error': 0, 'unknown': 0}
    for r in res:
        summary[r['health']] = summary.get(r['health'], 0) + 1
    gen_at = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    ts = datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    dxml = None
    if idg:
        fn = _load_diagram()
        if fn:
            try:
                d = fn(resource_group=rg or 'rg-ai-dev-we-01', output_path=f'{od}/infra-{sc}.drawio')
                if d and d.get('ok'):
                    dxml = d.get('drawio_xml') or d.get('xml') or d.get('content')
            except Exception:
                pass
    costs = _collect_costs(rg, s) if ic else None
    if costs and costs.get('by_rg'):
        rg_cost = {x['rg']: x['cost'] for x in costs['by_rg']}
        for r in res:
            if r['rg'] in rg_cost:
                r['cost'] = rg_cost[r['rg']]
    render = _load_render()
    if render:
        html = render(sc, res, summary, dxml, gen_at, costs)
    else:
        return {'ok': False, 'error': 'modulo dashboard.render non trovato'}
    try:
        os.makedirs(od, exist_ok=True)
        vp = os.path.join(od, f'dashboard-{sc}-{ts}.html')
        lp = os.path.join(od, f'dashboard-{sc}-latest.html')
        with open(vp, 'w', encoding='utf-8') as f:
            f.write(html)
        with open(lp, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        return {'ok': False, 'error': f'scrittura HTML fallita: {e}'}
    b64 = base64.b64encode(html.encode('utf-8')).decode('ascii')
    return {'ok': True, 'scope': sc, 'generated_at': gen_at, 'resources_checked': len(res),
            'summary': summary, 'versioned_path': vp, 'latest_path': lp,
            'diagram_included': bool(dxml), 'costs_included': bool(costs),
            'costs': costs, 'download_link': f'data:text/html;base64,{b64}',
            'html_size_bytes': len(html), 'results': res}

MANIFEST = {
    "tools": [
        {"name": "dashboard.generate", "doc": "[WRITE] Tool MASTER: genera una dashboard HTML versionata dell'infrastruttura Azure a livello di SUBSCRIPTION (o di un singolo RG). Orchestra i tool di controllo (SQL, Storage, ADF, Network, Databricks, Log Analytics, KeyVault, PE, NSG), aggrega lo stato in un modello normalizzato RICCO, integra i COSTI (corrente, forecast, giornaliero, per servizio, per RG, forecast giornaliero) e associa il costo del RG alle risorse, include il diagramma draw.io INLINE in cima e produce una UI con TAB (Overview/Costi/Forecast), filtri (ricerca, RG, tipo, stato), layout a griglia e card collassabili. Esclude LLM. Args: {\"resource_group\": str (opz), \"output_dir\": str (opz, default 'docs/dashboards'), \"include_diagram\": bool (opz, default true), \"include_costs\": bool (opz, default true)}.", "write": True, "entrypoint": "run"}
    ]
}
