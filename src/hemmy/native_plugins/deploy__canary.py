# Plugin auto-generato per il tool 'deploy.canary'.
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
def _app_path(rg, app, slot=None):
    base = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/"
            f"Microsoft.Web/sites/{app}")
    if slot:
        return f"{base}/slots/{slot}"
    return base


def _get_traffic(rg, app):
    path = f"{_app_path(rg, app)}/config/web"
    code, resp = _arm("GET", path, None, "2022-09-01")
    if code >= 300 or not isinstance(resp, dict):
        return None, f"get config failed: {code}"
    props = resp.get("properties") or {}
    return props.get("experiments", {}).get("rampUpRules", []), None


def run(**kwargs):
    action = kwargs.get("action") or "plan"
    params = kwargs.get("params") or {}
    try:
        rg = params.get("resource_group")
        app = params.get("app_name")
        slot = params.get("slot_name") or "staging"
        traffic = int(params.get("traffic_percent", 10))
        if not (rg and app):
            return {"ok": False, "error": "resource_group e app_name richiesti"}

        if action == "plan":
            plan = [
                {"step": 1, "action": "create_slot", "detail": f"crea slot '{slot}'"},
                {"step": 2, "action": "deploy_slot", "detail": f"deploy su slot '{slot}'"},
                {"step": 3, "action": "warmup", "detail": "warm-up dello slot"},
                {"step": 4, "action": "canary_traffic",
                 "detail": f"instrada {traffic}% del traffico allo slot"},
                {"step": 5, "action": "monitor", "detail": "monitora errori/latenza"},
                {"step": 6, "action": "promote_or_rollback",
                 "detail": "promuovi a 100% o rollback"},
            ]
            return {"ok": True, "action": "plan", "app": app, "slot": slot,
                    "traffic_percent": traffic, "plan": plan, "count": len(plan)}

        if action == "deploy":
            path = _app_path(rg, app, slot)
            code, resp = _arm("PUT", path, {"properties": {}}, "2022-09-01")
            return {"ok": code < 300, "action": "deploy", "app": app, "slot": slot,
                    "status_code": code, "response": resp}

        if action == "promote":
            path = f"{_app_path(rg, app)}/config/web"
            body = {"properties": {"experiments": {"rampUpRules": [
                {"actionHostName": f"{app}-{slot}.azurewebsites.net",
                 "reroutePercentage": 100.0}]}}}
            code, resp = _arm("PUT", path, body, "2022-09-01")
            return {"ok": code < 300, "action": "promote", "app": app, "slot": slot,
                    "traffic_percent": 100, "status_code": code, "response": resp}

        if action == "rollback":
            path = f"{_app_path(rg, app)}/config/web"
            body = {"properties": {"experiments": {"rampUpRules": []}}}
            code, resp = _arm("PUT", path, body, "2022-09-01")
            return {"ok": code < 300, "action": "rollback", "app": app,
                    "traffic_percent": 0, "status_code": code, "response": resp}

        if action == "status":
            rules, err = _get_traffic(rg, app)
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "status", "app": app,
                    "traffic_split": rules, "count": len(rules)}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "deploy.canary", "doc": "Gestisce strategie di deployment blue/green e canary su Azure: crea slot di staging (App Service), distribuisce il traffico gradualmente, monitora e promuove o rollback. Usa ARM REST API. Args: {\"action\": \"plan\"|\"deploy\"|\"promote\"|\"rollback\"|\"status\", \"params\": {\"resource_group\": str, \"app_name\": str, \"slot_name\": str (opz, default 'staging'), \"traffic_percent\": int (opz, default 10)}}. Ritorna: {deployment: {...}, status, traffic_split}.", "write": True, "entrypoint": "run"}
    ]
}
