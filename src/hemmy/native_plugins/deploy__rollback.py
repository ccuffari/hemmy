# Plugin auto-generato per il tool 'deploy.rollback'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error


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
        "User-Agent": "rollback",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _list_successful_runs(workflow=None, limit=10):
    path = f"/actions/runs?status=success&per_page={limit}"
    if workflow:
        path += f"&workflow_id={workflow}"
    code, resp = _gh_api(path)
    if code >= 300 or not isinstance(resp, dict):
        return [], f"list runs failed: {code}"
    runs = []
    for r in resp.get("workflow_runs", []):
        runs.append({"run_id": r.get("id"), "name": r.get("name"),
                     "head_sha": r.get("head_sha"), "head_branch": r.get("head_branch"),
                     "created_at": r.get("created_at"), "conclusion": r.get("conclusion")})
    return runs, None


def run(**kwargs):
    action = kwargs.get("action") or "plan"
    params = kwargs.get("params") or {}
    try:
        env = params.get("environment") or "dev"

        if action == "list_points":
            runs, err = _list_successful_runs(params.get("workflow"))
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "list_points", "environment": env,
                    "rollback_points": runs, "count": len(runs)}

        if action == "plan":
            runs, err = _list_successful_runs(params.get("workflow"))
            if err:
                return {"ok": False, "error": err}
            if not runs:
                return {"ok": False, "error": "nessuna run riuscita trovata"}
            target = runs[0]
            plan = [
                {"step": 1, "action": "revert_commit",
                 "detail": f"revert al commit {target['head_sha'][:8]} (run {target['run_id']})"},
                {"step": 2, "action": "trigger_pipeline",
                 "detail": f"rilancia apply su {env} dopo il revert"},
                {"step": 3, "action": "verify",
                 "detail": "verifica risorse su Azure"},
            ]
            return {"ok": True, "action": "plan", "environment": env,
                    "target_commit": target["head_sha"], "target_run": target["run_id"],
                    "rollback_plan": plan, "count": len(plan),
                    "note": "il rollback effettivo richiede approvazione"}

        if action == "execute":
            sha = params.get("commit_sha")
            if not sha:
                runs, err = _list_successful_runs(params.get("workflow"))
                if err or not runs:
                    return {"ok": False, "error": err or "nessuna run riuscita"}
                sha = runs[0]["head_sha"]
            return {"ok": True, "action": "execute", "environment": env,
                    "target_commit": sha, "status": "ready",
                    "note": ("esegui il revert via github (revert commit) e poi "
                             "cicd.trigger_pipeline apply")}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "deploy.rollback", "doc": "Gestisce il rollback di un deploy Terraform fallito: identifica l'ultima run CI/CD riuscita, propone il revert del commit o il ripristino dello state. WRITE: il rollback effettivo passa dall'approvazione. Args: {\"action\": \"plan\"|\"execute\"|\"list_points\", \"params\": {\"environment\": str (opz, default 'dev'), \"run_id\": str (opz), \"commit_sha\": str (opz)}}. Ritorna: {rollback_plan: [...], target_commit, status}.", "write": True, "entrypoint": "run"}
    ]
}
