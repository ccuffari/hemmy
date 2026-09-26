"""Autenticazione Azure condivisa per i tool nativi (inclusi quelli promossi
dal meta-tooling: usarla qui invece di reinventare l'auth in ogni plugin e'
proprio ciò che rende questo fix efficace su ~60 file in un colpo solo).

Ordine di risoluzione (STESSO principio di `infra/clients.py._credential()`,
il "choke point" già usato dai tool core):
  1. Sessione OAuth PER-UTENTE, se l'utente collegato al turno corrente ha
     completato il login Azure (bottone "Connetti Azure" nella UI): eredita
     esattamente i SUOI permessi. E' la via SEMPRE preferita in un contesto
     multi-tenant, perché isola gli utenti gli uni dagli altri.
  2. DefaultAzureCredential (Managed Identity / Azure CLI / Environment / ...)
     SOLO come fallback per l'uso locale a singolo operatore (CLI senza
     portale, `user_id=None`): mai in un processo web multi-utente, dove
     userebbe la STESSA identità per tutti gli utenti collegati.

PRIMA di questo fix, `get_arm_token()` chiamava sempre e solo
DefaultAzureCredential: su un servizio con una Managed Identity assegnata (o
credenziali `az login` residue sulla macchina), TUTTI gli utenti della
piattaforma avrebbero eseguito le azioni Azure di ~60 tool nativi (inclusi i
tool di SCRITTURA: cancel_run, rerun_pipeline, resources.pause,
deploy.rollback, backup.manage, policy.manage, tags.manage, functions.manage,
logicapps.manage, eventgrid.manage) con un'identità CONDIVISA — esattamente il
buco "Service Principal onnipotente" che il resto della piattaforma elimina.
"""

from __future__ import annotations

import json
import os
import urllib.request

_ARM_SCOPE = "https://management.azure.com/.default"
_ARM_ENDPOINT = "https://management.azure.com"
_SQL_SCOPE = "https://database.windows.net/.default"


def _get_token(scope: str) -> str:
    """Access token per `scope`: sessione OAuth dell'utente corrente se
    presente, altrimenti DefaultAzureCredential (solo fallback locale/CLI).
    Choke-point unico: aggiungere un nuovo scope (es. Key Vault, Storage)
    significa chiamare questa funzione, mai reimplementare l'auth nel tool."""
    try:
        from hemmy.auth.oauth_providers import get_current_azure_credential

        user_cred = get_current_azure_credential()
        if user_cred is not None:
            return user_cred.get_token(scope).token
    except Exception:  # noqa: BLE001
        pass

    from azure.identity import DefaultAzureCredential

    cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return cred.get_token(scope).token


def get_arm_token() -> str:
    """Access token per il management-plane ARM (`management.azure.com`)."""
    return _get_token(_ARM_SCOPE)


def get_sql_token() -> str:
    """Access token AAD per l'autenticazione a Azure SQL Database
    (`database.windows.net`) via `SQL_COPT_SS_ACCESS_TOKEN` in pyodbc."""
    return _get_token(_SQL_SCOPE)


def _list_subscriptions(token: str):
    url = _ARM_ENDPOINT + "/subscriptions?api-version=2022-12-01"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")).get("value", [])


def get_subscription_id() -> str:
    """Risolve la subscription di contesto senza usare ARM_SUBSCRIPTION_ID.

    Ordine di risoluzione:
    1. AZURE_SUBSCRIPTION_ID (contesto esplicito, non e' una credenziale).
    2. La prima subscription in stato 'Enabled' visibile all'identita'.
    """
    ctx = os.environ.get("AZURE_SUBSCRIPTION_ID")
    if ctx:
        return ctx

    token = get_arm_token()
    subs = _list_subscriptions(token)
    for s in subs:
        if s.get("state") == "Enabled":
            return s.get("subscriptionId")
    if subs:
        return subs[0].get("subscriptionId")
    raise RuntimeError("Nessuna subscription visibile all'identita' dell'agente")
