# Plugin auto-generato per il tool 'security.scan_iac'.
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
        "User-Agent": "finops-agent",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _gh_raw(path, ref=None):
    repo = _gh_repo()
    token = _gh_token()
    url = f"https://raw.githubusercontent.com/{repo}/{ref or 'main'}/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "finops-agent"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode(errors="replace")


def _finding(f, line, rule, severity, issue, rec):
    return {"file": f, "line": line, "rule": rule, "severity": severity,
            "issue": issue, "recommendation": rec}


RULES = [
    (r"public_network_access_enabled\s*=\s*true", "CKV_AZURE_1", "high",
     "public_network_access_enabled = true", "Imposta public_network_access_enabled = false o usa Private Endpoint"),
    (r"network_default_action\s*=\s*\"Allow\"", "CKV_AZURE_2", "high",
     "network_default_action = Allow", "Imposta network_default_action = 'Deny' e consenti solo subnet/IP noti"),
    (r"enable_rbac_authorization\s*=\s*false", "CKV_AZURE_3", "medium",
     "Key Vault senza RBAC", "Abilita enable_rbac_authorization = true"),
    (r"min_tls_version\s*=\s*\"TLS1_0\"", "CKV_AZURE_4", "high",
     "TLS 1.0", "Usa min_tls_version = 'TLS1_2'"),
    (r"https_traffic_only_enabled\s*=\s*false", "CKV_AZURE_5", "high",
     "HTTPS non obbligatorio", "Imposta https_traffic_only_enabled = true"),
    (r"enable_https_traffic_only\s*=\s*false", "CKV_AZURE_5", "high",
     "HTTPS non obbligatorio", "Imposta enable_https_traffic_only = true"),
    (r"purge_protection_enabled\s*=\s*false", "CKV_AZURE_6", "medium",
     "Key Vault senza purge protection", "Imposta purge_protection_enabled = true"),
    (r"soft_delete_retention_days\s*=\s*[0-6]\b", "CKV_AZURE_7", "low",
     "Soft delete retention bassa", "Usa soft_delete_retention_days >= 7"),
    (r"allow_blob_public_access\s*=\s*true", "CKV_AZURE_8", "high",
     "Blob public access abilitato", "Imposta allow_blob_public_access = false"),
    (r"(password|secret|key)\s*=\s*\"[^\"]{6,}\"", "SECRET_HARDCODED", "critical",
     "Possibile segreto hardcoded", "Usa Key Vault (data azurerm_key_vault_secret) o variabili"),
    (r"administrator_login_password\s*=\s*\"[^\"]+\"", "SECRET_SQL", "critical",
     "Password SQL in chiaro", "Usa Key Vault o var sensibile"),
    (r"storage_account_access_key\s*=\s*\"[^\"]+\"", "SECRET_STORAGE", "critical",
     "Storage key in chiaro", "Usa Key Vault o Managed Identity"),
]


def _scan_file(path, content):
    findings = []
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern, rule, sev, issue, rec in RULES:
            if re.search(pattern, line):
                findings.append(_finding(path, i, rule, sev, issue, rec))
    return findings


def run(**kwargs):
    action = kwargs.get("action") or "scan"
    params = kwargs.get("params") or {}
    try:
        base = params.get("path") or "environments"
        ref = params.get("ref")
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
                findings.append(_finding(f, 0, "READ_ERROR", "low", str(e), "verifica accesso al file"))
        by_sev = {}
        for x in findings:
            by_sev[x["severity"]] = by_sev.get(x["severity"], 0) + 1
        return {"ok": True, "action": action, "path": base, "files_scanned": len(files),
                "findings": findings, "count": len(findings), "by_severity": by_sev}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "security.scan_iac", "doc": "Scansiona il codice Terraform del repo per problemi di sicurezza e misconfigurazioni (stile Checkov/tfsec): storage/KV/SQL pubblici, mancanza di TLS, encryption, network ACL, secret hardcoded, versioning. Legge i file dal repo GitHub. Args: {\"action\": \"scan\"|\"scan_path\", \"params\": {\"path\": str (opz, default 'environments'), \"ref\": str (opz)}}. Ritorna findings normalizzati: {file, line, rule, severity, issue, recommendation}.", "write": False, "entrypoint": "run"}
    ]
}
