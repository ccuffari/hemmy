# Plugin auto-generato per il tool 'finops.unit_economics'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _arm_token():
    return get_arm_token()
def _arm(method, path, body=None, api_version=None):
    token = _arm_token()
    url = f"https://management.azure.com{path}"
    if api_version:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}api-version={api_version}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw.decode(errors="replace")
        return e.code, detail


def _sub():
    return get_subscription_id()
def _cost_by_resource(days=30):
    path = f"/subscriptions/{_sub()}/providers/Microsoft.CostManagement/query"
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ResourceId"}],
        },
    }
    code, resp = _arm("POST", path, body, "2023-11-01")
    if code >= 300 or not isinstance(resp, dict):
        return None, f"cost query failed: {code}"
    props = resp.get("properties") or {}
    cols = [c.get("name") for c in props.get("columns", [])]
    rows = props.get("rows") or []
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append({"resource_id": d.get("ResourceId"), "cost": round(d.get("Cost", 0), 4)})
    return out, None


def _metric_total(rid, metric, days=30):
    path = f"{rid}/providers/microsoft.insights/metrics"
    q = f"?metricnames={metric}&timespan=P{days}D&aggregation=Total&interval=P1D"
    code, resp = _arm("GET", path + q, None, "2023-10-01")
    if code >= 300 or not isinstance(resp, dict):
        return None
    total = 0.0
    for m in resp.get("value", []):
        for ts in m.get("timeseries", []):
            for dp in ts.get("data", []):
                v = dp.get("total")
                if v is not None:
                    total += v
    return total


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        days = int(params.get("lookback_days", 30))
        metric = params.get("metric") or "Transactions"
        rid = params.get("resource_id")

        if action in ("by_resource", "compute"):
            data, err = _cost_by_resource(days)
            if err:
                return {"ok": False, "error": err}
            out = []
            for item in data:
                r = item["resource_id"]
                if not r:
                    continue
                if rid and rid not in r:
                    continue
                units = _metric_total(r, metric, days)
                cpu = round(item["cost"] / units, 6) if units and units > 0 else None
                out.append({"resource": r, "total_cost": item["cost"],
                            "total_units": units, "metric": metric,
                            "cost_per_unit": cpu})
            out.sort(key=lambda x: x["total_cost"], reverse=True)
            return {"ok": True, "action": action, "unit_economics": out[:50],
                    "count": len(out), "currency": "EUR", "metric": metric}

        if action == "summary":
            data, err = _cost_by_resource(days)
            if err:
                return {"ok": False, "error": err}
            total = round(sum(x["cost"] for x in data), 2)
            return {"ok": True, "action": "summary", "total_cost": total,
                    "resources": len(data), "currency": "EUR",
                    "note": "usa action=by_resource per il costo unitario per risorsa"}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "finops.unit_economics", "doc": "Calcola il costo unitario (unit economics) delle risorse Azure: costo per transazione, per utente, per pipeline, per GB. Combina Cost Management API con metriche di utilizzo (transazioni storage, richieste, esecuzioni). Args: {\"action\": \"compute\"|\"by_resource\"|\"summary\", \"params\": {\"resource_id\": str (opz), \"metric\": str (opz, default 'Transactions'), \"lookback_days\": int (opz, default 30)}}. Ritorna: {unit_economics: [{resource, total_cost, total_units, cost_per_unit}], currency}.", "write": False, "entrypoint": "run"}
    ]
}
