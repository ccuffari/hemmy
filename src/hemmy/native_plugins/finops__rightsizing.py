# Plugin auto-generato per il tool 'finops.rightsizing'.
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
def _finding(rid, rtype, issue, severity, evidence, rec, saving=0.0):
    return {"resource_id": rid, "resource_type": rtype, "issue": issue,
            "severity": severity, "evidence": evidence, "recommendation": rec,
            "estimated_saving_eur": saving}


def _avg_metric(rid, metric, days=30, agg="Average"):
    timespan = f"P{days}D"
    path = f"{rid}/providers/microsoft.insights/metrics"
    q = f"?metricnames={metric}&timespan={timespan}&aggregation={agg}&interval=P1D"
    code, resp = _arm("GET", path + q, None, "2023-10-01")
    if code >= 300 or not isinstance(resp, dict):
        return None
    vals = []
    for m in resp.get("value", []):
        for ts in m.get("timeseries", []):
            for dp in ts.get("data", []):
                v = dp.get(agg.lower())
                if v is not None:
                    vals.append(v)
    return round(sum(vals) / len(vals), 2) if vals else None


def _scan_rg(rg, days=30):
    findings = []
    code, resp = _arm("GET", f"/subscriptions/{_sub()}/resourceGroups/{rg}/resources", None, "2021-04-01")
    if code >= 300 or not isinstance(resp, dict):
        return findings, {"error": f"list resources failed: {code}"}
    resources = resp.get("value", [])

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.sql/servers/databases":
            if (it.get("name") or "").endswith("/master"):
                continue
            dtu = _avg_metric(rid, "dtu_consumption_percent", days)
            cpu = _avg_metric(rid, "cpu_percent", days)
            usage = dtu if dtu is not None else cpu
            if usage is not None and usage < 10:
                findings.append(_finding(rid, "sql_database", "underutilized", "high",
                                         {"avg_dtu_or_cpu_percent": usage, "lookback_days": days},
                                         {"action": "downsize", "note": "consumo <10%: valuta SKU inferiore o serverless"},
                                         0.0))

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.compute/virtualmachines":
            cpu = _avg_metric(rid, "Percentage CPU", days)
            if cpu is not None and cpu < 5:
                findings.append(_finding(rid, "virtual_machine", "underutilized", "medium",
                                         {"avg_cpu_percent": cpu, "lookback_days": days},
                                         {"action": "downsize", "note": "CPU media <5%: valuta VM piu' piccola"},
                                         0.0))

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.storage/storageaccounts":
            tx = _avg_metric(rid, "Transactions", days, agg="Total")
            if tx is not None and tx < 100:
                findings.append(_finding(rid, "storage_account", "underutilized", "low",
                                         {"avg_transactions": tx, "lookback_days": days},
                                         {"action": "review", "note": "transazioni molto basse: valuta tier/ridondanza"},
                                         0.0))

    return findings, {"resources_scanned": len(resources)}


def run(**kwargs):
    action = kwargs.get("action") or "scan"
    params = kwargs.get("params") or {}
    try:
        rg = params.get("resource_group")
        days = int(params.get("lookback_days", 30))
        if action in ("scan", "scan_rg"):
            if not rg:
                return {"ok": False, "error": "resource_group richiesto"}
            findings, meta = _scan_rg(rg, days)
            total = round(sum(f.get("estimated_saving_eur", 0) for f in findings), 2)
            return {"ok": True, "action": action, "resource_group": rg,
                    "findings": findings, "count": len(findings),
                    "estimated_total_saving_eur": total, "meta": meta}

        if action == "summary":
            if not rg:
                return {"ok": False, "error": "resource_group richiesto"}
            findings, meta = _scan_rg(rg, days)
            by_sev = {}
            for f in findings:
                by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            return {"ok": True, "action": "summary", "resource_group": rg,
                    "total_findings": len(findings), "by_severity": by_sev, "meta": meta}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "finops.rightsizing", "doc": "Analizza il sottoutilizzo delle risorse Azure e consiglia il right-sizing (SKU/tier target) per FinOps. Usa ARM Metrics API + DMV SQL (autenticazione ereditata dal processo). Args: {\"action\": \"scan\"|\"scan_rg\"|\"summary\", \"params\": {\"resource_group\": str (opz), \"lookback_days\": int (opz, default 30)}}. Ritorna findings normalizzati: {resource_id, resource_type, issue, severity, evidence, recommendation, estimated_saving_eur}.", "write": False, "entrypoint": "run"}
    ]
}
