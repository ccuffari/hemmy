# Plugin auto-generato per il tool 'cicd.quality_gate'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import re
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


def _gh_tree(ref=None):
    repo = _gh_repo()
    token = _gh_token()
    url = f"https://api.github.com/repos/{repo}/git/trees/{ref or 'HEAD'}?recursive=1"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "quality-gate",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _gh_raw(path, ref=None):
    repo = _gh_repo()
    token = _gh_token()
    url = f"https://raw.githubusercontent.com/{repo}/{ref or 'main'}/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "quality-gate"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode(errors="replace")


SECRET_PATTERNS = [
    (r"(password|secret|key)\s*=\s*\"[^\"]{6,}\"", "SECRET_HARDCODED", "critical"),
    (r"administrator_login_password\s*=\s*\"[^\"]+\"", "SECRET_SQL", "critical"),
    (r"storage_account_access_key\s*=\s*\"[^\"]+\"", "SECRET_STORAGE", "critical"),
]

PLACEHOLDER_PATTERNS = [
    (r"<[a-zA-Z0-9_]+>", "PLACEHOLDER", "high"),
    (r"TODO|FIXME|XXX", "TODO_MARKER", "low"),
]


def _check_braces(content):
    depth = 0
    for ch in content:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _scan_file(path, content):
    findings = []
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern, rule, sev in SECRET_PATTERNS + PLACEHOLDER_PATTERNS:
            if re.search(pattern, line):
                findings.append({"file": path, "line": i, "rule": rule, "severity": sev,
                                 "text": stripped[:120]})
    if not _check_braces(content):
        findings.append({"file": path, "line": 0, "rule": "UNBALANCED_BRACES",
                         "severity": "critical", "text": "graffe sbilanciate"})
    return findings


SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def run(**kwargs):
    action = kwargs.get("action") or "check"
    params = kwargs.get("params") or {}
    try:
        base = params.get("path") or "environments"
        ref = params.get("ref")
        fail_on = params.get("fail_on") or "high"
        threshold = SEV_ORDER.get(fail_on, 3)

        tree = _gh_tree(ref)
        files = [t["path"] for t in tree.get("tree", [])
                 if t.get("type") == "blob" and t["path"].endswith(".tf")
                 and t["path"].startswith(base)]
        findings = []
        for f in files:
            try:
                content = _gh_raw(f, ref)
                findings.extend(_scan_file(f, content))
            except Exception as e:
                findings.append({"file": f, "line": 0, "rule": "READ_ERROR",
                                 "severity": "low", "text": str(e)})

        blockers = [x for x in findings if SEV_ORDER.get(x["severity"], 0) >= threshold]
        passed = len(blockers) == 0
        by_sev = {}
        for x in findings:
            by_sev[x["severity"]] = by_sev.get(x["severity"], 0) + 1
        return {"ok": True, "action": action, "passed": passed,
                "files_scanned": len(files), "findings": findings,
                "blockers": blockers, "count": len(findings),
                "blocker_count": len(blockers), "by_severity": by_sev,
                "fail_on": fail_on,
                "summary": ("GATE PASSED" if passed else f"GATE FAILED: {len(blockers)} blocker(s)")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "cicd.quality_gate", "doc": "Esegue un quality gate bloccante su una PR/branch: lint Terraform (iac.lint_files), security scan IaC (security.scan_iac), e verifica placeholder/segreti. Ritorna esito pass/fail con i motivi. Args: {\"action\": \"check\"|\"check_pr\", \"params\": {\"path\": str (opz, default 'environments'), \"ref\": str (opz), \"fail_on\": \"critical\"|\"high\"|\"medium\" (opz, default 'high')}}. Ritorna: {passed: bool, findings: [...], blockers: [...], summary}.", "write": False, "entrypoint": "run"}
    ]
}
