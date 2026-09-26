# Plugin auto-generato per il tool 'dashboard.render'.
# Installato via meta.install_tool con approvazione umana.

CSS = '*{box-sizing:border-box}body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#eef1f5;color:#1f2937}header{background:linear-gradient(135deg,#1f2937,#374151);color:#fff;padding:22px 32px}header h1{margin:0 0 4px;font-size:22px}header .meta{font-size:13px;opacity:.85}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;padding:18px 32px}.kpi{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center;cursor:pointer;transition:.15s;border:2px solid transparent}.kpi:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.12)}.kpi.active{border-color:#2563eb}.kpi .num{font-size:28px;font-weight:700}.kpi .lbl{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px}.filters{position:sticky;top:0;z-index:50;background:#fff;padding:14px 32px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}.filters input,.filters select{padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;background:#fff}.filters input{flex:1;min-width:200px}.filters .count{margin-left:auto;font-size:13px;color:#6b7280;font-weight:600}.section{padding:20px 32px}.section h2{font-size:18px;margin:0 0 12px}.diagram-wrap{background:#fff;border-radius:12px;padding:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:auto}.costs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}.cost-card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}.cost-card .lbl{font-size:12px;color:#6b7280;text-transform:uppercase}.cost-card .val{font-size:24px;font-weight:700;color:#059669}.chart-wrap{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-top:14px}.grid{padding:20px 32px 40px;display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:hidden;transition:.15s}.card:hover{box-shadow:0 4px 14px rgba(0,0,0,.12)}.card-head{display:flex;align-items:center;gap:10px;padding:14px 16px;cursor:pointer;flex-wrap:wrap;user-select:none}.badge{color:#fff;font-weight:700;font-size:11px;padding:3px 9px;border-radius:6px}.rname{font-weight:600;font-size:15px}.rtype{color:#6b7280;font-size:11px;background:#f3f4f6;padding:2px 8px;border-radius:4px}.rg-tag{color:#2563eb;font-size:11px;background:#eff6ff;padding:2px 8px;border-radius:4px}.cost-tag{margin-left:auto;font-weight:700;color:#059669;font-size:13px}.chev{color:#9ca3af;transition:.2s;font-size:12px}.card.open .chev{transform:rotate(180deg)}.card-body{display:none;padding:0 16px 16px}.card.open .card-body{display:block}table.metrics{width:100%;border-collapse:collapse;font-size:13px}table.metrics td{padding:5px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}table.metrics td.k{color:#6b7280;width:180px;font-weight:600}table.metrics td.v{color:#1f2937;word-break:break-all}.issues{margin-top:10px;padding:10px 12px;background:#fef2f2;border-radius:8px;color:#b91c1c;font-size:13px}.issues ul{margin:6px 0 0;padding-left:20px}table.data{width:100%;border-collapse:collapse;font-size:13px;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}table.data th{background:#f9fafb;text-align:left;padding:10px 14px;color:#6b7280;font-size:12px;text-transform:uppercase}table.data td{padding:9px 14px;border-top:1px solid #f3f4f6}.empty{padding:40px;text-align:center;color:#9ca3af}'

JS = "function applyFilters(){var q=document.getElementById('q').value.toLowerCase(),rg=document.getElementById('rg').value,ty=document.getElementById('type').value,he=document.getElementById('health').value,cs=document.querySelectorAll('.card'),sh=0;cs.forEach(function(c){var ok=true;if(q&&c.dataset.name.indexOf(q)===-1)ok=false;if(rg&&c.dataset.rg!==rg)ok=false;if(ty&&c.dataset.type!==ty)ok=false;if(he&&c.dataset.health!==he)ok=false;c.style.display=ok?'':'none';if(ok)sh++});document.getElementById('count').textContent=sh+' / '+cs.length+' risorse';document.getElementById('empty').style.display=sh===0?'block':'none'}function filterHealth(h){document.getElementById('health').value=(h==='all')?'':h;document.querySelectorAll('.kpi').forEach(function(k){k.classList.remove('active')});var el=document.querySelector('.kpi[data-filter=\"'+h+'\"]');if(el)el.classList.add('active');applyFilters()}function toggleCard(ev,id){ev.stopPropagation();var c=document.getElementById(id);if(c)c.classList.toggle('open')}applyFilters();"


def _fmt(v):
    try:
        return f'{float(v):.2f}'
    except Exception:
        return 'n/a'


def _chart_svg(fc_daily):
    if not fc_daily:
        return ''
    pts = [(str(x.get('date')), float(x.get('cost') or 0)) for x in fc_daily]
    if not pts:
        return ''
    w, h, pad = 900, 220, 40
    maxv = max(p[1] for p in pts) or 1
    n = len(pts)
    step = (w - 2 * pad) / max(n - 1, 1)
    coords = [(pad + i * step, h - pad - (v / maxv) * (h - 2 * pad)) for i, (_, v) in enumerate(pts)]
    poly = ' '.join(f'{x:.1f},{y:.1f}' for x, y in coords)
    area = f'{pad},{h-pad} ' + poly + f' {coords[-1][0]:.1f},{h-pad}'
    labels = ''
    for i, (d, _) in enumerate(pts):
        if i % 5 == 0 or i == n - 1:
            x = pad + i * step
            labels += f'<text x="{x:.0f}" y="{h-10}" font-size="10" fill="#9ca3af" text-anchor="middle">{d[4:6]}/{d[6:8]}</text>'
    return (f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto">'
            f'<polygon points="{area}" fill="#2563eb" opacity="0.12"/>'
            f'<polyline points="{poly}" fill="none" stroke="#2563eb" stroke-width="2"/>'
            f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#e5e7eb"/>'
            f'{labels}</svg>')


def render_html(scope, results, summary, diagram_xml, gen_at, costs=None):
    import json, base64
    C = {'ok': '#28a745', 'warn': '#ffc107', 'error': '#dc3545', 'unknown': '#6c757d'}
    rgs = sorted(set(r.get('rg', 'n/a') for r in results))
    tys = sorted(set(r.get('type', '') for r in results))
    ro = ''.join(f'<option value="{x}">{x}</option>' for x in rgs)
    to = ''.join(f'<option value="{x}">{x.split("/")[-1]}</option>' for x in tys)
    cards = []
    for idx, r in enumerate(results):
        c = C.get(r['health'], '#6c757d')
        rows = ''.join(f'<tr><td class="k">{k}</td><td class="v">{v}</td></tr>' for k, v in (r.get('metrics') or {}).items() if v is not None)
        iss = ''
        if r.get('issues'):
            iss = '<div class="issues"><b>Issue:</b><ul>' + ''.join(f'<li>{x}</li>' for x in r['issues']) + '</ul></div>'
        cost = r.get('cost')
        cost_tag = f'<span class="cost-tag">{_fmt(cost)} EUR</span>' if cost is not None else ''
        cid = f'card-{idx}'
        cards.append(f'<div class="card" id="{cid}" data-rg="{r.get("rg","")}" data-type="{r["type"]}" data-health="{r["health"]}" data-name="{r["resource"].lower()}" style="border-top:4px solid {c};"><div class="card-head" onclick="toggleCard(event,\'{cid}\')"><span class="badge" style="background:{c};">{r["health"].upper()}</span><span class="rname">{r["resource"]}</span><span class="rtype">{r["type"].split("/")[-1]}</span><span class="rg-tag">{r.get("rg","")}</span>{cost_tag}<span class="chev">&#9662;</span></div><div class="card-body"><table class="metrics">{rows}</table>{iss}</div></div>')
    dh = ''
    if diagram_xml:
        b64 = base64.b64encode(diagram_xml.encode('utf-8')).decode('ascii')
        dh = (f'<section class="section"><h2>Diagramma infrastruttura</h2>'
              f'<p><a download="infra.drawio" href="data:application/vnd.jgraph.mxfile;base64,{b64}">Scarica diagramma .drawio</a></p>'
              f'<div class="diagram-wrap"><div class="mxgraph" data-mxgraph=\'{{"highlight":"#0000ff","nav":true,"resize":true,"toolbar":"zoom layers lightbox","xml":{json.dumps(diagram_xml)}}}\'></div></div></section>'
              f'<script src="https://viewer.diagrams.net/js/viewer-static.min.js"></script>')
    costs = costs or {}
    cur = costs.get('current_cost')
    fc = costs.get('forecast')
    daily = costs.get('daily_cost')
    by_svc = costs.get('by_service') or []
    by_rg = costs.get('by_rg') or []
    fc_daily = costs.get('forecast_daily') or []
    svc_cards = ''.join(f'<div class="cost-card"><div class="lbl">{s.get("service")}</div><div class="val">{_fmt(s.get("cost"))} EUR</div></div>' for s in by_svc[:12])
    rg_rows = ''.join(f'<tr><td>{x.get("rg")}</td><td style="text-align:right;font-weight:600;color:#059669;">{_fmt(x.get("cost"))} EUR</td></tr>' for x in by_rg)
    chart = _chart_svg(fc_daily)
    cost_section = (f'<section class="section"><h2>Costi</h2><div class="costs-grid">'
                    f'<div class="cost-card"><div class="lbl">Costo corrente (mese)</div><div class="val">{_fmt(cur)} EUR</div></div>'
                    f'<div class="cost-card"><div class="lbl">Forecast fine mese</div><div class="val">{_fmt(fc)} EUR</div></div>'
                    f'<div class="cost-card"><div class="lbl">Costo medio giornaliero</div><div class="val">{_fmt(daily)} EUR</div></div>'
                    f'</div>'
                    + (f'<div class="chart-wrap"><h3 style="margin:0 0 8px;font-size:14px;color:#6b7280;">Forecast giornaliero</h3>{chart}</div>' if chart else '')
                    + (f'<h3 style="margin-top:24px;font-size:15px;">Costo per servizio</h3><div class="costs-grid">{svc_cards}</div>' if svc_cards else '')
                    + (f'<h3 style="margin-top:24px;font-size:15px;">Costo per Resource Group</h3><table class="data"><thead><tr><th>Resource Group</th><th style="text-align:right;">Costo</th></tr></thead><tbody>{rg_rows}</tbody></table>' if rg_rows else '')
                    + '</section>')
    cur_kpi = _fmt(cur) if cur is not None else 'n/a'
    return (f'<!DOCTYPE html><html lang="it"><head><meta charset="utf-8"><title>Infra Almanac - {scope}</title><style>{CSS}</style></head><body>'
            f'<header><h1>Infra Almanac &mdash; {scope}</h1><div class="meta">Generato: {gen_at} &middot; Risorse: {len(results)} &middot; Resource Group: {len(rgs)}</div></header>'
            f'<div class="summary">'
            f'<div class="kpi" data-filter="ok" onclick="filterHealth(\'ok\')"><div class="num" style="color:#28a745">{summary["ok"]}</div><div class="lbl">Healthy</div></div>'
            f'<div class="kpi" data-filter="warn" onclick="filterHealth(\'warn\')"><div class="num" style="color:#ffc107">{summary["warn"]}</div><div class="lbl">Warning</div></div>'
            f'<div class="kpi" data-filter="error" onclick="filterHealth(\'error\')"><div class="num" style="color:#dc3545">{summary["error"]}</div><div class="lbl">Error</div></div>'
            f'<div class="kpi" data-filter="all" onclick="filterHealth(\'all\')"><div class="num" style="color:#6c757d">{len(results)}</div><div class="lbl">Totale</div></div>'
            f'<div class="kpi"><div class="num" style="color:#059669">{cur_kpi}</div><div class="lbl">Costo EUR</div></div>'
            f'</div>'
            f'{cost_section}'
            f'{dh}'
            f'<section class="section"><h2>Risorse</h2>'
            f'<div class="filters"><input id="q" type="text" placeholder="Cerca per nome risorsa..." oninput="applyFilters()">'
            f'<select id="rg" onchange="applyFilters()"><option value="">Tutti i Resource Group</option>{ro}</select>'
            f'<select id="type" onchange="applyFilters()"><option value="">Tutti i tipi</option>{to}</select>'
            f'<select id="health" onchange="applyFilters()"><option value="">Tutti gli stati</option><option value="ok">Healthy</option><option value="warn">Warning</option><option value="error">Error</option></select>'
            f'<span class="count" id="count"></span></div>'
            f'<div class="grid" id="grid">{"".join(cards)}</div>'
            f'<div class="empty" id="empty" style="display:none;">Nessuna risorsa corrisponde ai filtri.</div>'
            f'</section>'
            f'<script>{JS}</script></body></html>')


def run(**kwargs):
    return {'ok': True, 'note': 'modulo helper: usa render_html(...) da dashboard.generate'}

MANIFEST = {
    "tools": [
        {"name": "dashboard.render", "doc": "[READ] Modulo helper di rendering per la dashboard infra: espone render_html(scope, results, summary, diagram_xml, gen_at, costs) che produce l'HTML completo (CSS+JS inline, filtri, griglia, diagramma, sezione costi con KPI + grafico forecast SVG + costo per servizio + costo per RG, costo per risorsa nelle card). Niente tab: tutto in un'unica vista. Non è pensato per essere chiamato direttamente dall'utente: è importato da dashboard.generate. Args: {}.", "write": False, "entrypoint": "run"}
    ]
}
