# Plugin auto-generato per il tool 'dora.metrics'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone


def _gh_token():
    # Choke-point condiviso `cicd_tools.resolve_github_token()`: sessione OAuth
    # per-utente (bottone "Connetti GitHub") prima di tutto, cosi' ogni utente
    # opera con la SUA identita' GitHub, non con un token di processo condiviso.
    # Nessun accesso diretto a env var qui: l'unico fallback (GITHUB_TOKEN/`gh
    # auth token`, per CI/automazione locale) vive dentro quel choke-point.
    try:
        from hemmy.tools.cicd.cicd_tools import resolve_github_token
        return resolve_github_token()
    except Exception:
        return None


def _gh_repo():
    return os.environ.get("GITHUB_REPOSITORY") or os.environ.get("GH_REPO")


def _gh_api(path):
    repo = _gh_repo()
    token = _gh_token()
    url = f"https://api.github.com/repos/{repo}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "dora-metrics",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _list_runs(days=30, workflow=None):
    path = f"/actions/runs?per_page=100"
    if workflow:
        path += f"&workflow_id={workflow}"
    code, resp = _gh_api(path)
    if code >= 300 or not isinstance(resp, dict):
        return [], f"list runs failed: {code}"
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    runs = []
    for r in resp.get("workflow_runs", []):
        created = _parse_dt(r.get("created_at"))
        if created and created.timestamp() >= cutoff:
            runs.append(r)
    return runs, None


def _list_prs(days=30):
    code, resp = _gh_api("/pulls?state=closed&per_page=100&sort=updated&direction=desc")
    if code >= 300 or not isinstance(resp, list):
        return [], f"list prs failed: {code}"
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    prs = []
    for p in resp:
        merged = _parse_dt(p.get("merged_at"))
        if merged and merged.timestamp() >= cutoff:
            prs.append(p)
    return prs, None


def _deployment_frequency(runs, days):
    successes = [r for r in runs if r.get("conclusion") == "success"]
    return round(len(successes) / max(days, 1), 2)


def _lead_time(prs):
    times = []
    for p in prs:
        created = _parse_dt(p.get("created_at"))
        merged = _parse_dt(p.get("merged_at"))
        if created and merged:
            times.append((merged - created).total_seconds() / 3600)
    return round(sum(times) / len(times), 2) if times else None


def _change_failure_rate(runs):
    total = len(runs)
    if total == 0:
        return None
    failed = len([r for r in runs if r.get("conclusion") == "failure"])
    return round(failed / total * 100, 2)


def _mttr(runs):
    sorted_runs = sorted(runs, key=lambda r: r.get("created_at") or "")
    recoveries = []
    last_fail = None
    for r in sorted_runs:
        if r.get("conclusion") == "failure":
            last_fail = _parse_dt(r.get("updated_at"))
        elif r.get("conclusion") == "success" and last_fail:
            succ = _parse_dt(r.get("updated_at"))
            if succ:
                recoveries.append((succ - last_fail).total_seconds() / 3600)
            last_fail = None
    return round(sum(recoveries) / len(recoveries), 2) if recoveries else None


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        days = int(params.get("lookback_days", 30))
        workflow = params.get("workflow")
        runs, err = _list_runs(days, workflow)
        if err:
            return {"ok": False, "error": err}
        prs, _ = _list_prs(days)

        metrics = {
            "deployment_frequency": _deployment_frequency(runs, days),
            "lead_time_hours": _lead_time(prs),
            "change_failure_rate": _change_failure_rate(runs),
            "mttr_hours": _mttr(runs),
        }

        if action == "deployment_frequency":
            return {"ok": True, "action": action,
                    "deployment_frequency": metrics["deployment_frequency"],
                    "period_days": days, "total_runs": len(runs)}
        if action == "lead_time":
            return {"ok": True, "action": action,
                    "lead_time_hours": metrics["lead_time_hours"],
                    "period_days": days, "total_prs": len(prs)}
        if action == "change_failure_rate":
            return {"ok": True, "action": action,
                    "change_failure_rate": metrics["change_failure_rate"],
                    "period_days": days, "total_runs": len(runs)}
        if action == "mttr":
            return {"ok": True, "action": action,
                    "mttr_hours": metrics["mttr_hours"],
                    "period_days": days}

        return {"ok": True, "action": "summary", "metrics": metrics,
                "period_days": days, "total_runs": len(runs), "total_prs": len(prs)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "dora.metrics", "doc": "Calcola le metriche DORA dal repo GitHub: Deployment Frequency, Lead Time for Changes, Change Failure Rate, MTTR. Usa GitHub Actions API (workflow runs, PR, commit). Args: {\"action\": \"summary\"|\"deployment_frequency\"|\"lead_time\"|\"change_failure_rate\"|\"mttr\", \"params\": {\"lookback_days\": int (opz, default 30), \"workflow\": str (opz)}}. Ritorna: {metrics: {deployment_frequency, lead_time_hours, change_failure_rate, mttr_hours}, period_days}.", "write": False, "entrypoint": "run"}
    ]
}
