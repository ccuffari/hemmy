# Plugin auto-generato per il tool 'finops.chargeback'.
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
def _cost_by_dimension(dimension, days=30):
    path = f"/subscriptions/{_sub()}/providers/Microsoft.CostManagement/query"
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": dimension}],
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
        out.append({"key": d.get(dimension) or "(untagged)",
                    "cost": round(d.get("Cost", 0), 2),
                    "currency": d.get("Currency", "EUR")})
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out, None


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        days = int(params.get("lookback_days", 30))

        if action == "by_rg":
            data, err = _cost_by_dimension("ResourceGroup", days)
            if err:
                return {"ok": False, "error": err}
            total = round(sum(x["cost"] for x in data), 2)
            return {"ok": True, "action": "by_rg", "breakdown": data,
                    "total": total, "currency": "EUR", "count": len(data)}

        if action in ("by_tag", "by_team"):
            tag_key = params.get("tag_key") or "owner"
            data, err = _cost_by_dimension("Tag", days)
            if err:
                return {"ok": False, "error": err}
            filtered = [x for x in data if tag_key.lower() in (x["key"] or "").lower()]
            total = round(sum(x["cost"] for x in filtered), 2)
            return {"ok": True, "action": action, "tag_key": tag_key,
                    "breakdown": filtered, "total": total, "currency": "EUR",
                    "count": len(filtered)}

        if action == "summary":
            by_rg, err1 = _cost_by_dimension("ResourceGroup", days)
            by_svc, err2 = _cost_by_dimension("ServiceName", days)
            if err1 and err2:
                return {"ok": False, "error": err1 or err2}
            return {"ok": True, "action": "summary",
                    "by_resource_group": by_rg or [],
                    "by_service": by_svc or [],
                    "total": round(sum(x["cost"] for x in (by_rg or [])), 2),
                    "currency": "EUR"}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "finops.chargeback", "doc": "Genera report di showback/chargeback dei costi Azure per team/progetto/tag. Aggrega i costi per tag (owner, project, environment) e per resource group. Usa Cost Management API + tags. Args: {\"action\": \"by_tag\"|\"by_rg\"|\"by_team\"|\"summary\", \"params\": {\"tag_key\": str (opz, default 'owner'), \"lookback_days\": int (opz, default 30)}}. Ritorna: {breakdown: [{key, cost, currency, resources}], total, currency}.", "write": False, "entrypoint": "run"}
    ]
}
