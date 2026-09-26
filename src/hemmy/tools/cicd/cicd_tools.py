"""Tool CI/CD — GitHub Actions e Azure DevOps (provider configurabile).

Filosofia: niente deployment a mano. Le pipeline eseguono Terraform (init/validate/
plan/apply/destroy) e vengono avviate su richiesta dell'utente.

- generate_pipeline: genera il file pipeline per il provider configurato (inerte).
- trigger_pipeline: avvia la pipeline con un comando Terraform (scrittura → approvazione).
- get_status: legge le esecuzioni recenti.

I segreti (GITHUB_TOKEN / AZDO_PAT) vengono letti dal .env e non sono mai esposti al
modello (redaction sulle Observation + non restituiti).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from hemmy.utils.helpers import redact_secrets


def resolve_github_token() -> str:
    """Recupera il token GitHub SENZA salvarlo, in ordine di priorità:
    1. login OAuth per-utente (bottone "Connetti GitHub", Device Flow) — l'identità
       dell'utente attualmente legato all'agente, se ha completato il login;
    2. GITHUB_TOKEN (se presente in ambiente, per CI/automazione);
    3. sessione della GitHub CLI (`gh auth token`, login personale sulla macchina).

    Il token resta in memoria per la singola operazione: non viene mai persistito.
    """
    try:
        from hemmy.auth.oauth_providers import get_current_github_token

        user_token = get_current_github_token()
        if user_token:
            return user_token
    except Exception:  # noqa: BLE001
        pass
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return ""

_TF_COMMANDS = ["init", "validate", "plan", "apply", "destroy"]
_MAX_LOG = 6000


# ------------------------------------------------------------------ HTTP helper


def _http(method: str, url: str, headers: dict[str, str], body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Impedisce a urllib di seguire i redirect (per gestirli a mano)."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def _download_text(url: str, headers: dict[str, str]) -> bytes:
    """Scarica un contenuto seguendo UN redirect firmato SENZA header di auth.

    L'endpoint log di GitHub risponde 302 verso un URL blob firmato: ripresentare
    l'header Authorization su quell'URL causa 401. Qui lo scarichiamo pulito.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(
            urllib.request.Request(url, headers=headers, method="GET"), timeout=60
        ) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location")
            if not location:
                raise RuntimeError(f"Redirect senza Location (HTTP {exc.code}).") from None
            # Seconda richiesta all'URL firmato, SENZA header di autenticazione.
            with urllib.request.urlopen(
                urllib.request.Request(location, method="GET"), timeout=60
            ) as resp2:
                return resp2.read()
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


# ------------------------------------------------------------ Pipeline templates


def _environments(cicd_config: dict[str, Any]) -> list[str]:
    return cicd_config.get("environments", ["dev", "uat", "prod"])


def _github_workflow(env: str, auth: str = "secret") -> str:
    """Workflow GitHub Actions DEDICATO a un ambiente (gira in environments/<env>).

    auth:
      - "secret": autenticazione azurerm con ARM_CLIENT_SECRET (secret nel repo).
      - "oidc":   Workload Identity Federation — nessun ARM_CLIENT_SECRET. Usa
                  azure/login@v2 + ARM_USE_OIDC=true; i soli ARM_CLIENT_ID/TENANT_ID/
                  SUBSCRIPTION_ID (identificatori, non segreti) restano come secret/vars.

    Best practice integrate: concurrency per-ambiente (no collisioni su lock/lease dello
    state), timeout-minutes (operazione appesa → fallimento con log), terraform validate
    prima del comando, nessun band-aid (-lock=false / state rm / import).
    """
    head = f"""name: Terraform {env}
on:
  workflow_dispatch:
    inputs:
      command:
        description: "Comando Terraform ({env})"
        required: true
        default: plan
        type: choice
        options: [init, validate, plan, apply, destroy]
# Serializza le run dello stesso ambiente: niente collisioni sul lock/lease dello state.
concurrency:
  group: terraform-{env}
  cancel-in-progress: false
permissions:
  contents: read
  id-token: write
jobs:
  terraform:
    runs-on: ubuntu-latest
    environment: {env}
    # Timeout esplicito: una operazione appesa fallisce con log, non viene uccisa muta.
    timeout-minutes: 30
    defaults:
      run:
        working-directory: environments/{env}
"""
    run_steps = """      - run: terraform init -input=false
      - run: terraform validate -no-color
      - run: terraform ${{ inputs.command }} -input=false ${{ (inputs.command == 'apply' || inputs.command == 'destroy') && '-auto-approve' || '' }}
"""
    if auth == "oidc":
        # OIDC: nessun client secret. azure/login stabilisce il token federato.
        body = """    env:
      ARM_USE_OIDC: "true"
      ARM_CLIENT_ID: ${{ secrets.ARM_CLIENT_ID }}
      ARM_TENANT_ID: ${{ secrets.ARM_TENANT_ID }}
      ARM_SUBSCRIPTION_ID: ${{ secrets.ARM_SUBSCRIPTION_ID }}
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.ARM_CLIENT_ID }}
          tenant-id: ${{ secrets.ARM_TENANT_ID }}
          subscription-id: ${{ secrets.ARM_SUBSCRIPTION_ID }}
      - uses: hashicorp/setup-terraform@v3
"""
    else:
        body = """    env:
      ARM_CLIENT_ID: ${{ secrets.ARM_CLIENT_ID }}
      ARM_CLIENT_SECRET: ${{ secrets.ARM_CLIENT_SECRET }}
      ARM_TENANT_ID: ${{ secrets.ARM_TENANT_ID }}
      ARM_SUBSCRIPTION_ID: ${{ secrets.ARM_SUBSCRIPTION_ID }}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
"""
    return head + body + run_steps


def _azuredevops_pipeline(env: str) -> str:
    """Pipeline Azure DevOps DEDICATA a un ambiente.

    Best practice: job con timeoutInMinutes (operazione appesa -> fallimento con log),
    step di validate prima del comando, nessun band-aid (-lock=false/state rm/import).
    """
    return f"""trigger: none
parameters:
  - name: command
    displayName: Comando Terraform ({env})
    type: string
    default: plan
    values: [init, validate, plan, apply, destroy]
pool:
  vmImage: ubuntu-latest
jobs:
  - job: terraform
    # Timeout esplicito: una operazione appesa fallisce con log, non resta bloccata.
    timeoutInMinutes: 30
    variables:
      - group: terraform-secrets-{env}   # ARM_CLIENT_ID/SECRET/TENANT_ID/SUBSCRIPTION_ID
    steps:
      - task: TerraformInstaller@1
        inputs:
          terraformVersion: latest
      - script: terraform init -input=false
        workingDirectory: environments/{env}
        env:
          ARM_CLIENT_ID: $(ARM_CLIENT_ID)
          ARM_CLIENT_SECRET: $(ARM_CLIENT_SECRET)
          ARM_TENANT_ID: $(ARM_TENANT_ID)
          ARM_SUBSCRIPTION_ID: $(ARM_SUBSCRIPTION_ID)
      - script: terraform validate -no-color
        workingDirectory: environments/{env}
      - script: terraform ${{{{ parameters.command }}}} -input=false
        workingDirectory: environments/{env}
        env:
          ARM_CLIENT_ID: $(ARM_CLIENT_ID)
          ARM_CLIENT_SECRET: $(ARM_CLIENT_SECRET)
          ARM_TENANT_ID: $(ARM_TENANT_ID)
          ARM_SUBSCRIPTION_ID: $(ARM_SUBSCRIPTION_ID)
"""


def _workflow_filename(env: str) -> str:
    return f"terraform-{env}.yml"


def render_pipelines(cicd_config: dict[str, Any]) -> dict[str, str]:
    """Restituisce {path: contenuto} delle pipeline per TUTTI gli ambienti configurati.

    Una pipeline dedicata per ambiente (dev/uat/prod), senza scrivere su disco: utile
    per committarle direttamente sul repo.
    """
    provider = cicd_config.get("provider", "github")
    auth = cicd_config.get("auth", "secret")
    files: dict[str, str] = {}
    for env in _environments(cicd_config):
        if provider == "github":
            files[f".github/workflows/{_workflow_filename(env)}"] = _github_workflow(env, auth)
        elif provider == "azure_devops":
            files[f"azure-pipelines-{env}.yml"] = _azuredevops_pipeline(env)
        else:
            raise ValueError(f"Provider CI/CD non supportato: '{provider}'.")
    return files


def render_pipeline(cicd_config: dict[str, Any], environment: str = "dev") -> dict[str, Any]:
    """Path+contenuto della pipeline di UN ambiente (senza scrivere su disco)."""
    if environment not in _environments(cicd_config):
        raise ValueError(f"Ambiente non configurato: '{environment}'.")
    provider = cicd_config.get("provider", "github")
    auth = cicd_config.get("auth", "secret")
    if provider == "github":
        return {
            "path": f".github/workflows/{_workflow_filename(environment)}",
            "content": _github_workflow(environment, auth),
        }
    if provider == "azure_devops":
        return {
            "path": f"azure-pipelines-{environment}.yml",
            "content": _azuredevops_pipeline(environment),
        }
    raise ValueError(f"Provider CI/CD non supportato: '{provider}'.")


def generate_pipeline(
    cicd_config: dict[str, Any], repo_root: str, environment: str | None = None
) -> dict[str, Any]:
    """Scrive su disco le pipeline: un ambiente specifico o TUTTI (dev/uat/prod)."""
    if environment:
        rendered = {render_pipeline(cicd_config, environment)["path"]: render_pipeline(cicd_config, environment)["content"]}
    else:
        rendered = render_pipelines(cicd_config)
    written = []
    for rel, content in rendered.items():
        path = os.path.join(repo_root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(rel)
    return {"provider": cicd_config.get("provider", "github"), "written": written}


def generate_oidc_federation(
    app_id: str | None = None,
    environments: list[str] | None = None,
    branch: str = "main",
    repo: str | None = None,
) -> dict[str, Any]:
    """Genera la configurazione di Workload Identity Federation (OIDC) per GitHub→Azure.

    OIDC elimina ARM_CLIENT_SECRET dalla pipeline: il runner ottiene un token federato
    che Azure AD scambia con un access token, tramite una *federated credential* legata
    a un subject specifico del repo. Questo tool è DETERMINISTICO: produce, per ogni
    subject, la federated credential (issuer/subject/audience) e il comando
    `az ad app federated-credential create` pronto da eseguire una-tantum.

    app_id: object/app id della app registration (SP) da federare; se omesso resta un
        placeholder <APP_ID> nei comandi.
    environments: ambienti GitHub da federare (default dev/uat/prod) → subject
        `repo:<owner/repo>:environment:<env>`.
    branch: branch da federare (subject `...:ref:refs/heads/<branch>`).
    repo: 'owner/repo' (default: GITHUB_REPO dal .env).
    """
    repo = repo or os.environ.get("GITHUB_REPO", "")
    if not repo:
        raise ValueError("Repo non specificato: passa repo='owner/repo' o imposta GITHUB_REPO.")
    envs = environments or ["dev", "uat", "prod"]
    issuer = "https://token.actions.githubusercontent.com"
    audience = "api://AzureADTokenExchange"
    app = app_id or "<APP_ID>"

    subjects: list[dict[str, str]] = []
    for env in envs:
        subjects.append({"name": f"github-{env}", "subject": f"repo:{repo}:environment:{env}"})
    subjects.append({"name": f"github-branch-{branch}", "subject": f"repo:{repo}:ref:refs/heads/{branch}"})

    creds: list[dict[str, Any]] = []
    commands: list[str] = []
    for s in subjects:
        params = {
            "name": s["name"],
            "issuer": issuer,
            "subject": s["subject"],
            "audiences": [audience],
        }
        creds.append(params)
        # NB: parametri come JSON single-line; l'operatore lo esegue una volta.
        params_json = json.dumps(params).replace('"', '\\"')
        commands.append(
            f'az ad app federated-credential create --id {app} '
            f'--parameters "{params_json}"'
        )

    return {
        "repo": repo,
        "app_id": app,
        "issuer": issuer,
        "audience": audience,
        "federated_credentials": creds,
        "az_commands": commands,
        "note": (
            "OIDC rimuove ARM_CLIENT_SECRET dalla pipeline. Passi: (1) esegui i comandi "
            "az (una-tantum) per creare le federated credential sull'app; (2) imposta i "
            "secret ARM_CLIENT_ID/ARM_TENANT_ID/ARM_SUBSCRIPTION_ID (identificatori, non "
            "segreti) via github.set_secret; (3) genera le pipeline con auth=oidc in "
            "cicd.yaml. La app deve avere il ruolo RBAC adeguato sulla subscription."
        ),
    }


# --------------------------------------------------------------- Trigger/status


def _github_settings(cicd_config: dict[str, Any]) -> tuple[str, str, str]:
    gh = cicd_config.get("github", {})
    token = resolve_github_token()
    repo = gh.get("repo") or os.environ.get("GITHUB_REPO", "")
    branch = gh.get("branch", "main")
    if not token or not repo:
        raise ValueError(
            "GitHub non autenticato: esegui 'gh auth login' (login personale) e imposta "
            "GITHUB_REPO (owner/repo)."
        )
    return token, repo, branch


def _ado_settings(cicd_config: dict[str, Any]) -> tuple[str, str, str, str]:
    ado = cicd_config.get("azure_devops", {})
    pat = os.environ.get("AZDO_PAT", "")
    org = ado.get("organization") or os.environ.get("AZDO_ORG", "")
    project = ado.get("project") or os.environ.get("AZDO_PROJECT", "")
    pipeline_id = str(ado.get("pipeline_id") or os.environ.get("AZDO_PIPELINE_ID", ""))
    if not pat or not org or not project or not pipeline_id:
        raise ValueError(
            "Azure DevOps non configurato: servono AZDO_PAT (.env), organization, "
            "project e pipeline_id."
        )
    return pat, org, project, pipeline_id


def trigger_pipeline(
    cicd_config: dict[str, Any], command: str = "plan", environment: str = "dev"
) -> dict[str, Any]:
    """[WRITE] Avvia la pipeline dell'AMBIENTE indicato con un comando Terraform.

    environment: dev | uat | prod (deve esistere la pipeline dedicata). Il deploy verso
    uat/prod va richiesto esplicitamente dall'utente.
    """
    if command not in _TF_COMMANDS:
        raise ValueError(f"Comando non valido: '{command}'. Usa uno di {_TF_COMMANDS}.")
    if environment not in _environments(cicd_config):
        raise ValueError(f"Ambiente non configurato: '{environment}'.")

    provider = cicd_config.get("provider", "github")
    if provider == "github":
        token, repo, branch = _github_settings(cicd_config)
        workflow = _workflow_filename(environment)
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "adf-agent",
            "Content-Type": "application/json",
        }
        status, _ = _http("POST", url, headers, {"ref": branch, "inputs": {"command": command}})
        return {"provider": "github", "environment": environment, "command": command, "http_status": status}

    if provider == "azure_devops":
        pat, org, project, pipeline_id = _ado_settings(cicd_config)
        url = f"https://dev.azure.com/{org}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.0"
        auth = base64.b64encode(f":{pat}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
        body = {"templateParameters": {"command": command, "environment": environment}}
        status, data = _http("POST", url, headers, body)
        return {"provider": "azure_devops", "environment": environment, "command": command, "run_id": data.get("id"), "http_status": status}

    raise ValueError(f"Provider CI/CD non supportato: '{provider}'.")


def get_status(cicd_config: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """Restituisce le esecuzioni recenti (tutte le pipeline del repo)."""
    provider = cicd_config.get("provider", "github")
    if provider == "github":
        token, repo, _ = _github_settings(cicd_config)
        url = f"https://api.github.com/repos/{repo}/actions/runs?per_page={limit}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "adf-agent",
        }
        _, data = _http("GET", url, headers)
        return [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "created_at": r.get("created_at"),
            }
            for r in data.get("workflow_runs", [])[:limit]
        ]

    if provider == "azure_devops":
        pat, org, project, pipeline_id = _ado_settings(cicd_config)
        url = f"https://dev.azure.com/{org}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.0"
        auth = base64.b64encode(f":{pat}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        _, data = _http("GET", url, headers)
        return [
            {"id": r.get("id"), "state": r.get("state"), "result": r.get("result")}
            for r in data.get("value", [])[:limit]
        ]

    raise ValueError(f"Provider CI/CD non supportato: '{provider}'.")


# ------------------------------------------------------- attesa / diagnosi run


def _gh_headers(cicd_config: dict[str, Any]) -> tuple[dict[str, str], str]:
    token, repo, _ = _github_settings(cicd_config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "adf-agent",
    }
    return headers, repo


def wait_for_run(
    cicd_config: dict[str, Any],
    run_id: str,
    timeout_seconds: int = 900,
    poll_interval: int = 15,
) -> dict[str, Any]:
    """Attende il completamento di una run CI/CD e ne riporta l'esito."""
    provider = cicd_config.get("provider", "github")
    deadline = time.time() + timeout_seconds

    while True:
        if provider == "github":
            headers, repo = _gh_headers(cicd_config)
            _, data = _http(
                "GET", f"https://api.github.com/repos/{repo}/actions/runs/{run_id}", headers
            )
            status, conclusion = data.get("status"), data.get("conclusion")
            done = status == "completed"
        elif provider == "azure_devops":
            pat, org, project, pipeline_id = _ado_settings(cicd_config)
            auth = base64.b64encode(f":{pat}".encode()).decode()
            headers = {"Authorization": f"Basic {auth}"}
            _, data = _http(
                "GET",
                f"https://dev.azure.com/{org}/{project}/_apis/pipelines/{pipeline_id}/runs/{run_id}?api-version=7.0",
                headers,
            )
            status, conclusion = data.get("state"), data.get("result")
            done = status == "completed"
        else:
            raise ValueError(f"Provider CI/CD non supportato: '{provider}'.")

        if done:
            return {"run_id": run_id, "status": status, "conclusion": conclusion, "timed_out": False}
        if time.time() >= deadline:
            return {"run_id": run_id, "status": status, "conclusion": conclusion, "timed_out": True}
        time.sleep(poll_interval)


def get_run_jobs(cicd_config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    """Job e step di una run (nome + conclusione) — identifica lo step fallito."""
    provider = cicd_config.get("provider", "github")
    if provider != "github":
        raise ValueError("get_run_jobs è implementato solo per GitHub.")
    headers, repo = _gh_headers(cicd_config)
    _, data = _http(
        "GET", f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs", headers
    )
    jobs = []
    for job in data.get("jobs", []):
        jobs.append(
            {
                "id": job.get("id"),
                "name": job.get("name"),
                "conclusion": job.get("conclusion"),
                "steps": [
                    {"name": s.get("name"), "conclusion": s.get("conclusion")}
                    for s in job.get("steps", [])
                ],
            }
        )
    return jobs


def get_run_logs(cicd_config: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Scarica i log dei job FALLITI di una run per diagnosticare la causa.

    Restituisce gli step falliti e il testo dei log (redatto dai segreti, troncato).
    """
    provider = cicd_config.get("provider", "github")
    if provider != "github":
        raise ValueError("get_run_logs è implementato solo per GitHub (ADO: usa il portale).")

    headers, repo = _gh_headers(cicd_config)
    jobs = get_run_jobs(cicd_config, run_id)
    failed = []
    logs_text = ""
    for job in jobs:
        failed_steps = [s["name"] for s in job["steps"] if s["conclusion"] == "failure"]
        if job["conclusion"] == "failure" or failed_steps:
            failed.append({"job": job["name"], "failed_steps": failed_steps})
            try:
                raw = _download_text(
                    f"https://api.github.com/repos/{repo}/actions/jobs/{job['id']}/logs",
                    headers,
                )
                logs_text += f"\n===== {job['name']} =====\n"
                logs_text += raw.decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                logs_text += f"\n(log di '{job['name']}' non disponibili: {exc})"

    logs_text = redact_secrets(logs_text)[-_MAX_LOG:]
    return {"run_id": run_id, "failed": failed, "logs": logs_text}
