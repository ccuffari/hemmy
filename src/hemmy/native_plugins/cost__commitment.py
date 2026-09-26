# Plugin auto-generato per il tool 'cost.commitment'.
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
def _cost_by_service(days=30):
    path = f"/subscriptions/{_sub()}/providers/Microsoft.CostManagement/query"
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ServiceName"}],
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
        out.append({"service": d.get("ServiceName"), "cost": round(d.get("Cost", 0), 2)})
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out, None


ELIGIBLE = {
    "Virtual Machines": 0.40,
    "SQL Database": 0.33,
    "Azure Cosmos DB": 0.20,
    "App Service": 0.30,
    "Azure Data Explorer": 0.30,
    "Redis Cache": 0.30,
    "Azure Databricks": 0.20,
}


def run(**kwargs):
    action = kwargs.get("action") or "analyze"
    params = kwargs.get("params") or {}
    try:
        days = int(params.get("lookback_days", 30))
        min_cost = float(params.get("min_monthly_cost", 10.0))
        data, err = _cost_by_service(days)
        if err:
            return {"ok": False, "error": err}

        recs = []
        for item in data:
            svc = item["service"] or ""
            cost = item["cost"]
            if cost < min_cost:
                continue
            for key, rate in ELIGIBLE.items():
                if key.lower() in svc.lower():
                    saving = round(cost * rate, 2)
                    recs.append({
                        "service": svc,
                        "current_monthly_cost": cost,
                        "potential_saving_eur": saving,
                        "discount_rate": rate,
                        "term": "1y o 3y (Reserved Instance / Savings Plan)",
                        "recommendation": f"valuta RI/SP su {svc}: risparmio stimato ~{int(rate*100)}%",
                    })
                    break

        total = round(sum(r["potential_saving_eur"] for r in recs), 2)
        if action == "recommendations":
            return {"ok": True, "action": action, "recommendations": recs,
                    "count": len(recs), "total_potential_saving_eur": total}

        if action == "summary":
            return {"ok": True, "action": "summary", "count": len(recs),
                    "total_potential_saving_eur": total, "top": recs[:5]}

        return {"ok": True, "action": "analyze", "recommendations": recs,
                "count": len(recs), "total_potential_saving_eur": total,
                "note": "stime indicative basate su tassi medi RI/SP"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "cost.commitment", "doc": "Analizza le opportunita' di risparmio tramite impegni (Reserved Instance / Savings Plan): identifica risorse con costo stabile e calcola il risparmio potenziale. Usa Cost Management API. Args: {\"action\": \"analyze\"|\"recommendations\"|\"summary\", \"params\": {\"lookback_days\": int (opz, default 30), \"min_monthly_cost\": float (opz, default 10.0)}}. Ritorna: {recommendations: [{resource, current_monthly, potential_saving, term}], total_potential_saving}.", "write": False, "entrypoint": "run"}
    ]
}
