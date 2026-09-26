# Plugin auto-generato per il tool 'remediation.run'.
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
REMEDIATION_MAP = {
    "unattached": {"action": "delete_disk", "risk": "medium"},
    "unassociated": {"action": "delete_public_ip", "risk": "medium"},
    "orphan": {"action": "delete_nic", "risk": "low"},
    "stopped_with_disks": {"action": "review_vm", "risk": "low"},
    "empty": {"action": "review_storage", "risk": "low"},
    "underutilized": {"action": "downsize", "risk": "high"},
    "cost_anomaly": {"action": "investigate_cost", "risk": "low"},
    "metric_anomaly": {"action": "investigate_metric", "risk": "low"},
    "empty_table": {"action": "alert_data", "risk": "low"},
    "null_in_key": {"action": "alert_data", "risk": "low"},
    "duplicates": {"action": "alert_data", "risk": "low"},
    "public_network_access_enabled = true": {"action": "fix_iac", "risk": "medium"},
    "network_default_action = Allow": {"action": "fix_iac", "risk": "medium"},
}


def _plan(findings):
    plan = []
    for f in findings:
        issue = f.get("issue") or f.get("rule") or "unknown"
        m = REMEDIATION_MAP.get(issue, {"action": "manual_review", "risk": "low"})
        plan.append({
            "finding": f,
            "action": m["action"],
            "risk": m["risk"],
            "params": f.get("recommendation", {}),
            "requires_approval": m["risk"] in ("medium", "high"),
        })
    return plan


def _execute_action(item):
    action = item["action"]
    f = item["finding"]
    rid = f.get("resource_id") or f.get("target")
    if action == "delete_disk" and rid:
        return _arm("DELETE", rid, None, "2023-10-02")
    if action == "delete_public_ip" and rid:
        return _arm("DELETE", rid, None, "2023-09-01")
    if action == "delete_nic" and rid:
        return _arm("DELETE", rid, None, "2023-09-01")
    if action == "downsize" and rid:
        return 200, {"note": "downsize richiede scelta SKU manuale", "resource": rid}
    if action in ("investigate_cost", "investigate_metric", "alert_data", "review_vm", "review_storage", "fix_iac", "manual_review"):
        return 200, {"note": f"azione '{action}' richiede intervento manuale", "resource": rid}
    return 400, {"error": f"azione non gestita: {action}"}


def run(**kwargs):
    action = kwargs.get("action") or "plan"
    params = kwargs.get("params") or {}
    try:
        findings = params.get("findings") or []
        dry_run = params.get("dry_run", True)

        if action == "plan":
            plan = _plan(findings)
            by_risk = {}
            for p in plan:
                by_risk[p["risk"]] = by_risk.get(p["risk"], 0) + 1
            return {"ok": True, "action": "plan", "plan": plan,
                    "count": len(plan), "by_risk": by_risk, "dry_run": True}

        if action == "execute":
            plan = _plan(findings)
            executed = []
            for item in plan:
                if dry_run:
                    executed.append({"action": item["action"], "status": "skipped_dry_run",
                                     "risk": item["risk"], "resource": item["finding"].get("resource_id")})
                    continue
                code, resp = _execute_action(item)
                executed.append({"action": item["action"], "status_code": code,
                                 "risk": item["risk"], "resource": item["finding"].get("resource_id"),
                                 "response": resp})
            return {"ok": True, "action": "execute", "executed": executed,
                    "count": len(executed), "dry_run": dry_run}

        if action == "verify":
            verified = []
            for f in findings:
                rid = f.get("resource_id")
                if not rid:
                    continue
                code, resp = _arm("GET", rid, None, "2021-04-01")
                verified.append({"resource": rid, "exists": code < 300, "status_code": code})
            return {"ok": True, "action": "verify", "verified": verified, "count": len(verified)}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "remediation.run", "doc": "Orchestratore di auto-remediation: consuma i findings dei tool di lettura (finops.waste, finops.rightsizing, anomaly.detect, dataquality.check, security.scan_iac) e propone/esegue azioni correttive con guardrail. State machine: rileva -> diagnostica -> propone -> esegue -> verifica. WRITE: ogni azione passa dall'approvazione umana. Args: {\"action\": \"plan\"|\"execute\"|\"verify\", \"params\": {\"findings\": [obj] (opz), \"resource_group\": str (opz), \"dry_run\": bool (opz, default true)}}. Ritorna: {plan: [{finding, action, params, risk}], executed: [...], verified: [...]}.", "write": True, "entrypoint": "run"}
    ]
}
