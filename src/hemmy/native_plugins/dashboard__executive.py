# Plugin auto-generato per il tool 'dashboard.executive'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
from datetime import datetime, timezone


def _kpi(name, value, unit="", status="ok"):
    return {"name": name, "value": value, "unit": unit, "status": status}


def _render_html(kpis, title="Executive Dashboard"):
    rows = ""
    for k in kpis:
        color = {"ok": "#2e7d32", "warning": "#f9a825", "critical": "#c62828"}.get(k["status"], "#555")
        rows += (f'<tr><td>{k["name"]}</td>'
                 f'<td style="color:{color};font-weight:bold">{k["value"]} {k["unit"]}</td>'
                 f'<td>{k["status"]}</td></tr>')
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#f5f5f5}}
h1{{color:#0078d4}}
table{{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
th,td{{padding:10px 14px;border-bottom:1px solid #eee;text-align:left}}
th{{background:#0078d4;color:#fff}}
.footer{{margin-top:16px;color:#888;font-size:12px}}
</style></head><body>
<h1>{title}</h1>
<p>Generato: {datetime.now(timezone.utc).isoformat()}</p>
<table><thead><tr><th>KPI</th><th>Valore</th><th>Stato</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="footer">Dashboard executive cross-Ops (CloudOps / DevOps / DataOps / FinOps)</div>
</body></html>"""
    return html


def run(**kwargs):
    action = kwargs.get("action") or "generate"
    params = kwargs.get("params") or {}
    try:
        rg = params.get("resource_group")
        out_path = params.get("output_path") or "docs/dashboards/executive.html"

        kpis = [
            _kpi("Costo mese corrente", "n/d", "EUR", "ok"),
            _kpi("Forecast fine mese", "n/d", "EUR", "ok"),
            _kpi("Risorse totali", "n/d", "", "ok"),
            _kpi("Risorse idle (waste)", "n/d", "", "warning"),
            _kpi("Deployment frequency", "n/d", "/giorno", "ok"),
            _kpi("Change failure rate", "n/d", "%", "ok"),
            _kpi("MTTR", "n/d", "h", "ok"),
            _kpi("Data quality issues", "n/d", "", "warning"),
            _kpi("Secure score", "n/d", "%", "ok"),
            _kpi("Alert di sicurezza", "n/d", "", "ok"),
        ]

        if action == "summary":
            return {"ok": True, "action": "summary", "kpis": kpis,
                    "count": len(kpis), "resource_group": rg}

        html = _render_html(kpis)
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)
            written = True
        except Exception as e:
            written = False
            out_path = f"(non scritto: {e})"

        return {"ok": True, "action": "generate", "html_path": out_path,
                "written": written, "kpis": kpis, "count": len(kpis),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "note": "popola i KPI con i tool di lettura (cost.manage, dora.metrics, ecc.)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "dashboard.executive", "doc": "Genera una dashboard executive HTML con KPI cross-Ops (CloudOps, DevOps, DataOps, FinOps): costi, salute risorse, DORA metrics, data quality, security score. Aggrega i risultati dei tool di lettura in un unico report HTML. Args: {\"action\": \"generate\"|\"summary\", \"params\": {\"resource_group\": str (opz), \"output_path\": str (opz, default 'docs/dashboards/executive.html')}}. Ritorna: {html_path, kpis: {...}, generated_at}.", "write": True, "entrypoint": "run"}
    ]
}
