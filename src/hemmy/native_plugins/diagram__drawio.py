# Plugin auto-generato per il tool 'diagram.drawio'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
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
AZURE_SHAPE = {
    'Microsoft.Storage/storageAccounts': 'mxgraph.azure.storage_accounts',
    'Microsoft.Sql/servers': 'mxgraph.azure.sql_servers',
    'Microsoft.Sql/servers/databases': 'mxgraph.azure.sql_databases',
    'Microsoft.DataFactory/factories': 'mxgraph.azure.data_factory',
    'Microsoft.Databricks/workspaces': 'mxgraph.azure.databricks',
    'Microsoft.KeyVault/vaults': 'mxgraph.azure.key_vaults',
    'Microsoft.OperationalInsights/workspaces': 'mxgraph.azure.log_analytics',
    'Microsoft.Network/virtualNetworks': 'mxgraph.azure.virtual_networks',
    'Microsoft.Network/privateEndpoints': 'mxgraph.azure.private_endpoint',
    'Microsoft.Network/networkSecurityGroups': 'mxgraph.azure.network_security_groups',
}

STYLE = {
    'Microsoft.Storage/storageAccounts': ('#dae8fc', '#6c8ebf', 'Storage'),
    'Microsoft.Sql/servers': ('#d5e8d4', '#82b366', 'SQL Server'),
    'Microsoft.Sql/servers/databases': ('#d5e8d4', '#82b366', 'SQL DB'),
    'Microsoft.DataFactory/factories': ('#ffe6cc', '#d79b00', 'Data Factory'),
    'Microsoft.Databricks/workspaces': ('#e1d5e7', '#9673a6', 'Databricks'),
    'Microsoft.KeyVault/vaults': ('#fff2cc', '#d6b656', 'Key Vault'),
    'Microsoft.OperationalInsights/workspaces': ('#f8cecc', '#b85450', 'Log Analytics'),
    'Microsoft.Network/virtualNetworks': ('#dae8fc', '#6c8ebf', 'VNet'),
    'Microsoft.Network/privateEndpoints': ('#dae8fc', '#6c8ebf', 'Private Endpoint'),
    'Microsoft.Network/networkSecurityGroups': ('#f8cecc', '#b85450', 'NSG'),
}

GRID = [
    ['CICD', 'IaC', None],
    ['Data Landing', 'Data Transformation', 'Data Storage'],
    ['Logging', 'Security', 'IAM'],
]

LAYER_COLORS = {
    'CICD': '#e6e6e6',
    'IaC': '#e6e6e6',
    'Data Landing': '#dae8fc',
    'Data Transformation': '#ffe6cc',
    'Data Storage': '#d5e8d4',
    'Security': '#fff2cc',
    'Logging': '#f8cecc',
    'IAM': '#fff2cc',
    'Networking': '#e1d5e7',
    'Altro': '#f5f5f5',
}

DEFAULT_LAYERS = {
    'Data Landing': ['Microsoft.Storage/storageAccounts'],
    'Data Transformation': ['Microsoft.DataFactory/factories', 'Microsoft.Databricks/workspaces'],
    'Data Storage': ['Microsoft.Sql/servers', 'Microsoft.Sql/servers/databases'],
    'Security': ['Microsoft.KeyVault/vaults'],
    'Logging': ['Microsoft.OperationalInsights/workspaces'],
    'Networking': ['Microsoft.Network/virtualNetworks', 'Microsoft.Network/privateEndpoints', 'Microsoft.Network/networkSecurityGroups'],
}


def _style_for(rtype):
    for k, v in STYLE.items():
        if rtype.lower().startswith(k.lower()):
            return v
    return ('#f5f5f5', '#666666', rtype.split('/')[-1])


def _shape_for(rtype):
    for k, v in AZURE_SHAPE.items():
        if rtype.lower().startswith(k.lower()):
            return v
    return None


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def run(**kwargs):
    rg = kwargs.get('resource_group')
    output_path = kwargs.get('output_path', 'docs/infrastructure.drawio')
    include_network = kwargs.get('include_network', True)
    include_nsg_rules = kwargs.get('include_nsg_rules', True)
    include_acl = kwargs.get('include_acl', True)
    include_data_flows = kwargs.get('include_data_flows', True)
    include_diagnostics = kwargs.get('include_diagnostics', True)
    user_layers = kwargs.get('layers') or {}
    static_nodes = kwargs.get('static_nodes') or {}
    if not rg:
        return {'ok': False, 'error': 'resource_group mancante'}
    token, err = _token()
    if err:
        return {'ok': False, 'error': err}
    try:
        sub = _sub()
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    base = f'/subscriptions/{sub}/resourceGroups/{rg}'

    r = _api('GET', f'{base}/resources?api-version=2021-04-01', None, token)
    if 'error' in r:
        return {'ok': False, 'stage': 'list_resources', 'status': r['status'], 'error': r['error']}
    resources = r['body'].get('value', [])

    vnets = []
    if include_network:
        rv = _api('GET', f'{base}/providers/Microsoft.Network/virtualNetworks?api-version=2023-09-01', None, token)
        if 'error' not in rv:
            vnets = rv['body'].get('value', [])

    pes = []
    if include_network:
        rp = _api('GET', f'{base}/providers/Microsoft.Network/privateEndpoints?api-version=2023-09-01', None, token)
        if 'error' not in rp:
            pes = rp['body'].get('value', [])

    nsgs = []
    if include_network:
        rn = _api('GET', f'{base}/providers/Microsoft.Network/networkSecurityGroups?api-version=2023-09-01', None, token)
        if 'error' not in rn:
            nsgs = rn['body'].get('value', [])

    storage_acls = []
    if include_acl:
        for res in resources:
            if res.get('type', '').lower() == 'microsoft.storage/storageaccounts':
                rid = res.get('id')
                ra = _api('GET', f'{rid}?api-version=2023-01-01', None, token)
                if 'error' not in ra:
                    props = ra['body'].get('properties', {})
                    net = props.get('networkAcls', {})
                    storage_acls.append({
                        'name': res.get('name'),
                        'default_action': net.get('defaultAction'),
                        'bypass': net.get('bypass'),
                        'ip_rules': [x.get('value') for x in net.get('ipRules', [])],
                        'vnet_rules': [x.get('id') for x in net.get('virtualNetworkRules', [])],
                    })

    kv_acls = []
    if include_acl:
        for res in resources:
            if res.get('type', '').lower() == 'microsoft.keyvault/vaults':
                rid = res.get('id')
                ra = _api('GET', f'{rid}?api-version=2023-07-01', None, token)
                if 'error' not in ra:
                    props = ra['body'].get('properties', {})
                    net = props.get('networkAcls', {})
                    kv_acls.append({
                        'name': res.get('name'),
                        'default_action': net.get('defaultAction'),
                        'bypass': net.get('bypass'),
                        'ip_rules': [x.get('value') for x in net.get('ipRules', [])],
                        'vnet_rules': [x.get('id') for x in net.get('virtualNetworkRules', [])],
                    })

    diag_links = []
    if include_diagnostics:
        for res in resources:
            rid = res.get('id')
            rd = _api('GET', f'{rid}/providers/Microsoft.Insights/diagnosticSettings?api-version=2021-05-01-preview', None, token)
            if 'error' not in rd:
                for ds in rd['body'].get('value', []):
                    ws = ds.get('properties', {}).get('workspaceId')
                    if ws:
                        diag_links.append({'resource': res.get('name'), 'workspace': ws.split('/')[-1]})

    adf_flows = []
    if include_data_flows:
        for res in resources:
            if res.get('type', '').lower() == 'microsoft.datafactory/factories':
                fname = res.get('name')
                fbase = f'{base}/providers/Microsoft.DataFactory/factories/{fname}'
                rls = _api('GET', f'{fbase}/linkedservices?api-version=2018-06-01', None, token)
                ls_map = {}
                if 'error' not in rls:
                    for ls in rls['body'].get('value', []):
                        ls_map[ls.get('name')] = ls.get('properties', {}).get('type')
                rds = _api('GET', f'{fbase}/datasets?api-version=2018-06-01', None, token)
                ds_map = {}
                if 'error' not in rds:
                    for ds in rds['body'].get('value', []):
                        props = ds.get('properties', {})
                        lsref = props.get('linkedServiceName', {}).get('referenceName')
                        ds_map[ds.get('name')] = {'ls': lsref, 'ls_type': ls_map.get(lsref)}
                rps = _api('GET', f'{fbase}/pipelines?api-version=2018-06-01', None, token)
                if 'error' not in rps:
                    for pl in rps['body'].get('value', []):
                        pname = pl.get('name')
                        acts = pl.get('properties', {}).get('activities', [])
                        for act in acts:
                            if act.get('type') == 'Copy':
                                src = act.get('inputs', [{}])[0].get('referenceName') if act.get('inputs') else None
                                snk = act.get('outputs', [{}])[0].get('referenceName') if act.get('outputs') else None
                                adf_flows.append({
                                    'factory': fname, 'pipeline': pname, 'activity': act.get('name'),
                                    'source_dataset': src, 'source_ls': ds_map.get(src, {}).get('ls'),
                                    'source_type': ds_map.get(src, {}).get('ls_type'),
                                    'sink_dataset': snk, 'sink_ls': ds_map.get(snk, {}).get('ls'),
                                    'sink_type': ds_map.get(snk, {}).get('ls_type'),
                                })

    def layer_of(res):
        name = res.get('name')
        rtype = res.get('type', '')
        if user_layers:
            for lname, members in user_layers.items():
                if name in members:
                    return lname
            return 'Altro'
        for lname, types in DEFAULT_LAYERS.items():
            for t in types:
                if rtype.lower().startswith(t.lower()):
                    return lname
        return 'Altro'

    layers_map = {}
    for res in resources:
        lname = layer_of(res)
        layers_map.setdefault(lname, []).append(res)

    cells = []
    cid = [2]

    def add_container(label, x, y, w, h, color):
        i = cid[0]; cid[0] += 1
        cells.append(
            f'<mxCell id="{i}" value="{_esc(label)}" style="rounded=1;whiteSpace=wrap;html=1;fillColor={color};opacity=25;strokeColor=#666666;dashed=1;verticalAlign=top;fontStyle=1;fontSize=14;" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return i

    def add_node(label, x, y, w, h, fill, stroke, shape=None):
        i = cid[0]; cid[0] += 1
        if shape:
            style = (f'shape={shape};whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};'
                     f'verticalLabelPosition=bottom;verticalAlign=top;fontSize=11;')
        else:
            style = f'rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=11;'
        cells.append(
            f'<mxCell id="{i}" value="{_esc(label)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return i

    def add_edge(src, dst, label='', dashed=False, color='#333333'):
        i = cid[0]; cid[0] += 1
        dash = 'dashed=1;' if dashed else ''
        cells.append(
            f'<mxCell id="{i}" value="{_esc(label)}" style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;{dash}strokeColor={color};endArrow=block;endFill=1;fontSize=10;" edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        return i

    node_ids = {}
    CELL_W = 500
    CELL_H = 200
    GRID_X0 = 40
    GRID_Y0 = 40
    NODE_W, NODE_H = 60, 60
    NODE_SPACING = 130
    PAD_TOP = 50

    cell_pos = {}
    for ri, row in enumerate(GRID):
        for ci, lname in enumerate(row):
            if lname:
                cell_pos[lname] = (ri, ci)

    for lname, (ri, ci) in cell_pos.items():
        x = GRID_X0 + ci * CELL_W
        y = GRID_Y0 + ri * CELL_H
        color = LAYER_COLORS.get(lname, '#f5f5f5')
        add_container(lname, x, y, CELL_W - 20, CELL_H - 20, color)

    for lname, members in layers_map.items():
        if lname not in cell_pos:
            continue
        ri, ci = cell_pos[lname]
        x0 = GRID_X0 + ci * CELL_W
        y0 = GRID_Y0 + ri * CELL_H
        for mi, res in enumerate(members):
            rtype = res.get('type', '')
            name = res.get('name', '')
            fill, stroke, kind = _style_for(rtype)
            shape = _shape_for(rtype)
            nx = x0 + 30 + mi * NODE_SPACING
            ny = y0 + PAD_TOP
            nid = add_node(f'{kind}\n{name}', nx, ny, NODE_W, NODE_H, fill, stroke, shape=shape)
            node_ids[res.get('id', name)] = nid
            node_ids[name] = nid

    for lname, nodes in static_nodes.items():
        if lname not in cell_pos:
            continue
        ri, ci = cell_pos[lname]
        x0 = GRID_X0 + ci * CELL_W
        y0 = GRID_Y0 + ri * CELL_H
        for mi, sn in enumerate(nodes):
            sname = sn.get('name', '') if isinstance(sn, dict) else str(sn)
            sicon = sn.get('icon') if isinstance(sn, dict) else None
            nx = x0 + 30 + mi * NODE_SPACING
            ny = y0 + PAD_TOP
            nid = add_node(sname, nx, ny, NODE_W, NODE_H, '#f5f5f5', '#666666', shape=sicon)
            node_ids[sname] = nid

    net_x = GRID_X0 + 3 * CELL_W + 20
    net_y = GRID_Y0
    net_h = 3 * CELL_H - 20
    if include_network and vnets:
        add_container('Networking', net_x, net_y, 300, net_h, LAYER_COLORS['Networking'])
        for vi, v in enumerate(vnets):
            vname = v.get('name')
            vprops = v.get('properties', {})
            subnets = vprops.get('subnets', [])
            vid = add_node(f'VNet: {vname}', net_x + 30, net_y + 50 + vi * 200, 200, 50, '#dae8fc', '#6c8ebf')
            node_ids[vname] = vid
            for si, sn in enumerate(subnets):
                sname = sn.get('name')
                sprops = sn.get('properties', {})
                ses = sprops.get('serviceEndpoints', [])
                se_labels = ', '.join([s.get('service') for s in ses]) if ses else 'nessun SE'
                sid = add_node(f'subnet: {sname}\n[{se_labels}]', net_x + 30, net_y + 120 + vi * 200 + si * 90, 240, 50, '#dae8fc', '#6c8ebf')
                node_ids[sname] = sid
                add_edge(vid, sid, 'contiene', dashed=True, color='#6c8ebf')
                for se in ses:
                    svc = se.get('service', '')
                    for res in resources:
                        rt = res.get('type', '').lower()
                        if ('storage' in svc.lower() and 'storage' in rt) or \
                           ('sql' in svc.lower() and 'sql' in rt) or \
                           ('keyvault' in svc.lower() and 'keyvault' in rt):
                            if res.get('name') in node_ids:
                                add_edge(sid, node_ids[res.get('name')], f'SE: {svc}', dashed=True, color='#6c8ebf')

    for pi, pe in enumerate(pes):
        pname = pe.get('name')
        pprops = pe.get('properties', {})
        conns = pprops.get('privateLinkServiceConnections', []) or pprops.get('manualPrivateLinkServiceConnections', [])
        pid = add_node(f'PE: {pname}', net_x + 30, net_y + 400 + pi * 90, 240, 50, '#dae8fc', '#6c8ebf')
        node_ids[pname] = pid
        for c in conns:
            target = c.get('properties', {}).get('privateLinkServiceId', '')
            tname = target.split('/')[-1] if target else ''
            if tname in node_ids:
                add_edge(pid, node_ids[tname], 'private link', color='#6c8ebf')

    for ni, nsg in enumerate(nsgs):
        nname = nsg.get('name')
        nprops = nsg.get('properties', {})
        rules = nprops.get('securityRules', [])
        rule_lines = []
        if include_nsg_rules:
            for rule in rules:
                rp = rule.get('properties', {})
                rule_lines.append(
                    f"{rp.get('direction')} {rp.get('access')} {rp.get('protocol')} "
                    f"{rp.get('destinationPortRange') or rp.get('destinationPortRanges')} prio={rp.get('priority')}"
                )
        label = f'NSG: {nname}'
        if rule_lines:
            label += '\n' + '\n'.join(rule_lines[:6])
        nid = add_node(label, net_x + 30, net_y + 500 + ni * 110, 240, 90, '#f8cecc', '#b85450')
        node_ids[nname] = nid

    acl_y = GRID_Y0 + 3 * CELL_H + 20
    for ai, acl in enumerate(storage_acls + kv_acls):
        label = (f"ACL: {acl['name']}\ndefault={acl['default_action']} bypass={acl['bypass']}\n"
                 f"ip={len(acl['ip_rules'])} vnet={len(acl['vnet_rules'])}")
        aid = add_node(label, GRID_X0 + ai * 320, acl_y, 300, 70, '#fff2cc', '#d6b656')
        if acl['name'] in node_ids:
            add_edge(aid, node_ids[acl['name']], 'ACL', dashed=True, color='#d6b656')
        for vr in acl['vnet_rules']:
            sub_name = vr.split('/')[-1] if vr else ''
            if sub_name in node_ids:
                add_edge(aid, node_ids[sub_name], 'vnet rule', dashed=True, color='#d6b656')

    for dl in diag_links:
        if dl['resource'] in node_ids and dl['workspace'] in node_ids:
            add_edge(node_ids[dl['resource']], node_ids[dl['workspace']], 'diagnostic', dashed=True, color='#b85450')

    for fi, flow in enumerate(adf_flows):
        label = f"{flow['pipeline']}.{flow['activity']}\n{flow['source_type']} -> {flow['sink_type']}"
        fid = add_node(label, GRID_X0 + fi * 320, acl_y + 100, 300, 60, '#ffe6cc', '#d79b00')
        for key in [flow.get('source_ls'), flow.get('sink_ls')]:
            if key and key in node_ids:
                add_edge(fid, node_ids[key], 'data flow', color='#d79b00')

    xml = (
        '<mxfile host="app.diagrams.net" modified="2026-01-01T00:00:00.000Z" agent="azure-diagram-tool" version="24.0.0">\n'
        f'  <diagram id="infra-{_esc(rg)}" name="{_esc(rg)}">\n'
        '    <mxGraphModel dx="1422" dy="798" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="2400" pageHeight="1600" math="0" shadow="0">\n'
        '      <root>\n'
        '        <mxCell id="0"/>\n'
        '        <mxCell id="1" parent="0"/>\n'
        + '\n'.join('        ' + c for c in cells) + '\n'
        '      </root>\n'
        '    </mxGraphModel>\n'
        '  </diagram>\n'
        '</mxfile>\n'
    )

    return {
        'ok': True,
        'resource_group': rg,
        'output_path': output_path,
        'resources_count': len(resources),
        'layers': {k: [r.get('name') for r in v] for k, v in layers_map.items()},
        'static_nodes': static_nodes,
        'vnets_count': len(vnets),
        'private_endpoints_count': len(pes),
        'nsgs_count': len(nsgs),
        'storage_acls_count': len(storage_acls),
        'kv_acls_count': len(kv_acls),
        'diag_links_count': len(diag_links),
        'adf_flows_count': len(adf_flows),
        'adf_flows': adf_flows,
        'drawio_xml': xml,
        'note': 'Layout a griglia 3x3 + Networking trasversale. Nodi statici via static_nodes={layer: [{name, icon}]}.',
    }

MANIFEST = {
    "tools": [
        {"name": "diagram.drawio", "doc": "[WRITE] Genera un file draw.io (.drawio XML mxGraph) dell'infrastruttura Azure reale con layout a GRIGLIA: CICD/IaC in cima, Landing/Transformation/Storage in orizzontale al centro, Logging/Security/IAM sotto, Networking trasversale. Icone native draw.io (mxgraph.azure.*). Supporta NODI STATICI per layer non-Azure (es. Terraform, GitHub Actions, IAM). Collegamenti direzionali (service endpoint, VNet rule, private link, ACL, diagnostic settings, flussi ADF). Legge lo stato da Azure via ARM REST API (autenticazione ereditata dal processo). Args: {\"resource_group\": str, \"output_path\": str (opz), \"layers\": {layer_name: [resource_name,...]} (opz), \"static_nodes\": {layer_name: [{\"name\": str, \"icon\": str (opz)}]} (opz), \"include_network\": bool, \"include_nsg_rules\": bool, \"include_acl\": bool, \"include_data_flows\": bool, \"include_diagnostics\": bool}. Restituisce XML + riepilogo.", "write": True, "entrypoint": "run"}
    ]
}
