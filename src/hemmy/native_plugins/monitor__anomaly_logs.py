# Plugin auto-generato per il tool 'monitor.anomaly_logs'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import statistics
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
def _finding(target, issue, severity, evidence, rec):
    return {"target": target, "issue": issue, "severity": severity,
            "evidence": evidence, "recommendation": rec}


def _zscore_anomalies(values, z_threshold=2.5):
    if len(values) < 5:
        return []
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return []
    out = []
    for i, v in enumerate(values):
        z = (v - mean) / stdev
        if abs(z) > z_threshold:
            out.append((i, v, round(z, 2)))
    return out


def _kql(workspace_id, query, hours=24):
    path = (f"/subscriptions/{_sub()}/resourceGroups/"
            f"{os.environ.get('LOG_ANALYTICS_RG', 'rg-ai-dev-we-01')}/providers/"
            f"Microsoft.OperationalInsights/workspaces/"
            f"{os.environ.get('LOG_ANALYTICS_WORKSPACE', 'log-ai-dev-we-01')}/query")
    body = {"query": query, "timespan": f"PT{hours}H"}
    code, resp = _arm("POST", path, body, "2017-01-01-preview")
    if code >= 300 or not isinstance(resp, dict):
        return None, f"kql failed: {code}"
    tables = resp.get("tables") or []
    if not tables:
        return [], None
    cols = [c.get("name") for c in tables[0].get("columns", [])]
    rows = [dict(zip(cols, r)) for r in tables[0].get("rows", [])]
    return rows, None


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        hours = int(params.get("lookback_hours", 24))
        z = float(params.get("z_threshold", 2.5))
        findings = []

        if action in ("errors", "summary"):
            q = ("AzureDiagnostics | where TimeGenerated > ago(24h) "
                 "| summarize cnt=count() by bin(TimeGenerated, 1h) | order by TimeGenerated asc")
            rows, err = _kql(None, q, hours)
            if err:
                findings.append(_finding("logs/errors", "query_error", "low", {"error": err},
                                         "verifica workspace Log Analytics"))
            elif rows:
                vals = [r.get("cnt", 0) for r in rows]
                anom = _zscore_anomalies(vals, z)
                for i, v, zz in anom:
                    findings.append(_finding("logs/errors", "error_spike", "high" if zz > 3 else "medium",
                                             {"hour_index": i, "count": v, "z_score": zz,
                                              "mean": round(statistics.mean(vals), 2)},
                                             "picco di errori nei log: verifica la causa"))

        if action == "exceptions":
            q = ("AppExceptions | where TimeGenerated > ago(24h) "
                 "| summarize cnt=count() by bin(TimeGenerated, 1h) | order by TimeGenerated asc")
            rows, err = _kql(None, q, hours)
            if err:
                findings.append(_finding("logs/exceptions", "query_error", "low", {"error": err},
                                         "verifica workspace"))
            elif rows:
                vals = [r.get("cnt", 0) for r in rows]
                anom = _zscore_anomalies(vals, z)
                for i, v, zz in anom:
                    findings.append(_finding("logs/exceptions", "exception_spike", "high",
                                             {"hour_index": i, "count": v, "z_score": zz},
                                             "picco di eccezioni: verifica stack trace"))

        if action == "latency":
            q = ("AppRequests | where TimeGenerated > ago(24h) "
                 "| summarize avg_ms=avg(DurationMs) by bin(TimeGenerated, 1h) | order by TimeGenerated asc")
            rows, err = _kql(None, q, hours)
            if err:
                findings.append(_finding("logs/latency", "query_error", "low", {"error": err},
                                         "verifica workspace"))
            elif rows:
                vals = [r.get("avg_ms", 0) for r in rows]
                anom = _zscore_anomalies(vals, z)
                for i, v, zz in anom:
                    findings.append(_finding("logs/latency", "latency_spike", "medium",
                                             {"hour_index": i, "avg_ms": v, "z_score": zz},
                                             "latenza anomala: verifica dipendenze/DB"))

        by_sev = {}
        for f in findings:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        return {"ok": True, "action": action, "findings": findings,
                "count": len(findings), "by_severity": by_sev,
                "z_threshold": z, "lookback_hours": hours}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "monitor.anomaly_logs", "doc": "Rileva anomalie nei log Azure via Log Analytics (KQL): picchi di errori, eccezioni, latenze anomale, pattern insoliti. Usa metodo statistico (z-score) sui conteggi per intervallo. Args: {\"action\": \"errors\"|\"exceptions\"|\"latency\"|\"summary\", \"params\": {\"workspace_name\": str (opz), \"resource_group\": str (opz), \"lookback_hours\": int (opz, default 24), \"z_threshold\": float (opz, default 2.5)}}. Ritorna findings normalizzati: {target, issue, severity, evidence, recommendation}.", "write": False, "entrypoint": "run"}
    ]
}
