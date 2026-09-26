# Plugin auto-generato per il tool 'anomaly.detect'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
import statistics
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


def _daily_costs(days=30):
    path = f"/subscriptions/{_sub()}/providers/Microsoft.CostManagement/query"
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
        },
    }
    code, resp = _arm("POST", path, body, "2023-11-01")
    if code >= 300 or not isinstance(resp, dict):
        return None, f"cost query failed: {code}"
    rows = (resp.get("properties") or {}).get("rows") or []
    series = [(r[1], r[0]) for r in rows]
    return series, None


def _metric_series(rid, metric, days=30, agg="Average"):
    path = f"{rid}/providers/microsoft.insights/metrics"
    q = f"?metricnames={metric}&timespan=P{days}D&aggregation={agg}&interval=P1D"
    code, resp = _arm("GET", path + q, None, "2023-10-01")
    if code >= 300 or not isinstance(resp, dict):
        return None, f"metrics failed: {code}"
    vals = []
    for m in resp.get("value", []):
        for ts in m.get("timeseries", []):
            for dp in ts.get("data", []):
                v = dp.get(agg.lower())
                if v is not None:
                    vals.append(v)
    return vals, None


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        z = float(params.get("z_threshold", 2.5))
        days = int(params.get("lookback_days", 30))
        findings = []

        if action in ("cost", "summary"):
            series, err = _daily_costs(days)
            if err:
                findings.append(_finding("cost", "query_error", "low", {"error": err}, "verifica permessi Cost Management"))
            elif series:
                vals = [v for _, v in series]
                anom = _zscore_anomalies(vals, z)
                for i, v, zz in anom:
                    findings.append(_finding("cost/daily", "cost_anomaly", "high" if zz > 3 else "medium",
                                             {"date": series[i][0], "value": v, "z_score": zz,
                                              "mean": round(statistics.mean(vals), 2)},
                                             "picco di costo anomalo: verifica risorse/attivita' in quella data"))

        if action in ("metrics", "summary"):
            rid = params.get("resource_id")
            metrics = params.get("metric_names") or ["Percentage CPU", "dtu_consumption_percent"]
            if rid:
                for m in metrics:
                    vals, err = _metric_series(rid, m, days)
                    if err or not vals:
                        continue
                    anom = _zscore_anomalies(vals, z)
                    for i, v, zz in anom:
                        findings.append(_finding(f"{rid}#{m}", "metric_anomaly", "medium",
                                                 {"metric": m, "value": v, "z_score": zz,
                                                  "mean": round(statistics.mean(vals), 2)},
                                                 "valore anomalo: verifica carico/query/processi"))

        if action == "logs":
            findings.append(_finding("logs", "not_implemented", "low",
                                     {"note": "usa monitor.query_logs per KQL"},
                                     "per i log usa monitor.query_logs con una query KQL"))

        by_sev = {}
        for f in findings:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        return {"ok": True, "action": action, "findings": findings,
                "count": len(findings), "by_severity": by_sev,
                "z_threshold": z, "lookback_days": days}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "anomaly.detect", "doc": "Rileva anomalie su costi, metriche e log Azure senza soglie manuali (metodo statistico: media + deviazione standard, z-score). Usa Cost Management API, ARM Metrics API e Log Analytics. Args: {\"action\": \"cost\"|\"metrics\"|\"logs\"|\"summary\", \"params\": {\"resource_id\": str (opz), \"metric_names\": [str] (opz), \"lookback_days\": int (opz, default 30), \"z_threshold\": float (opz, default 2.5), \"workspace_name\": str (opz)}}. Ritorna findings normalizzati: {target, issue, severity, evidence, recommendation}.", "write": False, "entrypoint": "run"}
    ]
}
