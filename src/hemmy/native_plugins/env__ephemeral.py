# Plugin auto-generato per il tool 'env.ephemeral'.
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


def _gh_api(path, method="GET", body=None):
    repo = _gh_repo()
    token = _gh_token()
    url = f"https://api.github.com/repos/{repo}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ephemeral-env",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _list_ephemeral_branches():
    code, resp = _gh_api("/branches?per_page=100")
    if code >= 300 or not isinstance(resp, list):
        return [], f"list branches failed: {code}"
    out = []
    for b in resp:
        name = b.get("name", "")
        if name.startswith("ephemeral/"):
            out.append({"branch": name, "sha": (b.get("commit") or {}).get("sha")})
    return out, None


def run(**kwargs):
    action = kwargs.get("action") or "list"
    params = kwargs.get("params") or {}
    try:
        base_env = params.get("base_env") or "dev"
        ttl = int(params.get("ttl_hours", 24))

        if action == "list":
            envs, err = _list_ephemeral_branches()
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "list", "environments": envs,
                    "count": len(envs)}

        if action == "create":
            pr = params.get("pr_number")
            if not pr:
                return {"ok": False, "error": "pr_number richiesto"}
            branch = f"ephemeral/pr-{pr}"
            plan = [
                {"step": 1, "action": "create_branch", "detail": f"crea branch {branch}"},
                {"step": 2, "action": "commit_env",
                 "detail": f"genera environments/ephemeral-pr-{pr} da {base_env}"},
                {"step": 3, "action": "trigger_pipeline",
                 "detail": f"plan+apply su ambiente effimero (TTL {ttl}h)"},
                {"step": 4, "action": "schedule_teardown",
                 "detail": f"teardown automatico dopo {ttl}h"},
            ]
            return {"ok": True, "action": "create", "environment": f"ephemeral-pr-{pr}",
                    "branch": branch, "base_env": base_env, "ttl_hours": ttl,
                    "plan": plan, "count": len(plan),
                    "note": "la creazione effettiva richiede approvazione"}

        if action == "destroy":
            pr = params.get("pr_number")
            if not pr:
                return {"ok": False, "error": "pr_number richiesto"}
            branch = f"ephemeral/pr-{pr}"
            plan = [
                {"step": 1, "action": "trigger_pipeline",
                 "detail": f"destroy su ambiente effimero pr-{pr}"},
                {"step": 2, "action": "delete_branch", "detail": f"elimina branch {branch}"},
            ]
            return {"ok": True, "action": "destroy", "environment": f"ephemeral-pr-{pr}",
                    "branch": branch, "plan": plan, "count": len(plan),
                    "note": "il destroy effettivo richiede approvazione"}

        if action == "status":
            envs, err = _list_ephemeral_branches()
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "status", "active": envs,
                    "count": len(envs), "ttl_hours": ttl}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "env.ephemeral", "doc": "Gestisce ambienti effimeri (ephemeral) per test isolati su PR: crea/distrugge un ambiente Terraform temporaneo con nome univoco, applica e poi fa teardown. WRITE: creazione e distruzione passano dall'approvazione. Args: {\"action\": \"create\"|\"destroy\"|\"list\"|\"status\", \"params\": {\"pr_number\": int (opz), \"base_env\": str (opz, default 'dev'), \"ttl_hours\": int (opz, default 24)}}. Ritorna: {environment, status, resources, ttl}.", "write": True, "entrypoint": "run"}
    ]
}
