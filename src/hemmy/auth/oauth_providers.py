"""OAuth per-utente verso i provider cloud/DevOps (Azure, Azure DevOps, GitHub,
GCP, AWS) — niente più Service Principal condiviso, PAT/chiavi incollate,
service account JSON, né `az login`/`gh auth login` globali al processo.

Principio: l'utente clicca "Connetti", viene mandato al PORTALE del provider
(Microsoft/GitHub/Google/AWS), concede il consenso con la SUA identità, e
l'agente eredita esattamente i permessi che quell'utente ha lì — non un SP
onnipotente, non un token condiviso.

UX UNIFORME — Device Authorization Flow (RFC 8628) per TUTTI i provider: il
backend mostra "vai su questa pagina, inserisci questo codice" e fa polling in
un thread finché l'utente completa il consenso nel suo browser. Nessun redirect
URI da registrare, nessun listener/callback pubblico da esporre su
`portal.hemmy.it`: la stessa identica logica funziona invariata in locale e in
produzione. Prima di questo fix Azure usava `MSAL.acquire_token_interactive`,
che apre un browser e un listener HTTP sulla STESSA macchina del processo
Python: funzionava solo perché backend e browser giravano sullo stesso host in
sviluppo locale, ma è irrimediabilmente rotto su un backend hostato (nessun
browser sul server, nessun modo per l'utente di raggiungere un listener legato
al loopback del container). Device Flow risolve questo E allinea Azure allo
stesso pattern già in uso per GitHub.

Sicurezza — coerente col resto del progetto ("nessuna traccia di credenziali"):
- I token vivono SOLO in memoria di processo, in dizionari tenuti qui, mai su disco.
- Sono associati a un `user_id` (l'utente della piattaforma) e spariscono al logout,
  alla disconnessione esplicita o al riavvio del processo.
- Non vengono mai passati al modello LLM (restano dentro l'infrastruttura client).

Azure: MSAL (Microsoft Authentication Library) Device Code Flow. Client id di
default: quello PUBBLICO e multi-tenant di Azure CLI
(04b07795-8ddb-461a-bbee-02f9e1bf7b46, lo stesso che usa `az login`) — pensato
da Microsoft per essere riusato da terze parti, pre-consentito nella maggior
parte dei tenant per gli scope ARM/KeyVault/Storage/SQL di base: nessuna App
Registration dedicata necessaria. `AZURE_OAUTH_CLIENT_ID` per usarne una propria.

Azure DevOps: NON è un provider separato da connettere — riusa la sessione
Azure già attiva (stesso account Entra ID) chiedendo silenziosamente un token
aggiuntivo per lo scope `499b84ac-1321-427f-aa17-267ca6975798/.default`
(resource id ufficiale di Azure DevOps in Entra ID, lo stesso usato da `az
devops`): niente nuovo consenso interattivo nella maggior parte dei tenant.
L'utente indica solo il nome della propria organization (non un segreto).

GitHub: Device Authorization Flow di una OAuth App. In ottica BYOK, il Client ID
NON è più una variabile d'ambiente di piattaforma: ogni utente crea la propria
OAuth App (con "Device Flow" abilitato, nessun client secret richiesto) e la
salva nel proprio profilo Hemmy (`user_secrets["github_oauth_client_id"]`). Il
backend usa il Client ID dell'utente per avviare il Device Flow. Un fallback
opzionale all'env var `GITHUB_OAUTH_CLIENT_ID` resta disponibile se si vuole
offrire un default di piattaforma: se presente, viene usato solo per gli utenti
che non hanno configurato il proprio.

GCP: Device Authorization Flow OAuth 2.0 di Google ("TV and Limited-Input
Device" client). A differenza di GitHub/Azure, Google richiede un client
SECRET anche per questo tipo di client pubblico (particolarità di Google, non
di questo progetto) — `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`,
entrambi solo lato backend, mai esposti al frontend/modello.

AWS: Device Authorization Flow di AWS IAM Identity Center (SSO OIDC) — l'UNICO
modo che AWS offre per un login OAuth-like che restituisca credenziali
temporanee per-utente. Richiede che l'ORGANIZZAZIONE del cliente abbia Identity
Center attivo (l'utente fornisce l'URL del proprio portale SSO, es.
`https://azienda.awsapps.com/start` — non un valore fisso dell'agente, a
differenza degli altri provider). Se l'organizzazione non ha Identity Center,
questo flusso non è utilizzabile: `auth/cloud_credentials.py` resta disponibile
come fallback esplicito con chiavi statiche (access key/secret), per scelta
consapevole dell'utente in quel caso.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

# ------------------------------------------------------------------ stato in-memory
# Nessuna persistenza su disco: alla chiusura del processo tutto sparisce.
_LOCK = threading.Lock()
_AZURE_SESSIONS: dict[Any, "AzureUserCredential"] = {}
_AZURE_PENDING: dict[Any, dict[str, Any]] = {}
_AZURE_DEVOPS_ORGS: dict[Any, str] = {}
_GITHUB_SESSIONS: dict[Any, dict[str, Any]] = {}
_GITHUB_PENDING: dict[Any, dict[str, Any]] = {}
_GCP_SESSIONS: dict[Any, dict[str, Any]] = {}
_GCP_PENDING: dict[Any, dict[str, Any]] = {}
_AWS_SSO_SESSIONS: dict[Any, dict[str, Any]] = {}
_AWS_ROLE_CREDS: dict[Any, dict[str, Any]] = {}
_AWS_PENDING: dict[Any, dict[str, Any]] = {}

# Ponte con clients.py: permette a `clients._credential()`/`cicd_tools` di
# trovare la sessione Azure/GitHub dell'utente CORRENTE senza dover cambiare la
# firma di decine di funzioni client.
#
# SICUREZZA: questo era in origine un semplice `_current_user_id: Any = None` di
# MODULO — una variabile globale di processo. Con più utenti concorrenti (ogni
# turno gira nel proprio `threading.Thread`, vedi `interfaces/web.py._run_turn`)
# questo permetteva una race condition seria: mentre il turno dell'utente A è in
# esecuzione in un thread e sta per chiamare `get_current_azure_credential()`,
# l'arrivo di una richiesta dell'utente B poteva sovrascrivere la variabile
# globale prima che A la leggesse — risultato: le azioni di A potevano girare
# con le credenziali Azure/GitHub di B (o viceversa). `threading.local()` dà a
# OGNI thread la propria copia: dato che ogni turno ha un thread dedicato che
# imposta l'utente corrente PRIMA di eseguire qualunque tool, l'isolamento è
# garantito anche sotto concorrenza reale tra utenti diversi.
_local = threading.local()

# Client id pubblico di Azure CLI (multi-tenant, riuso di terze parti previsto da
# Microsoft — è lo stesso identificativo usato da `az login`).
_AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_ARM_SCOPE = "https://management.azure.com/user_impersonation"
# Resource id ufficiale di Azure DevOps in Entra ID (lo stesso usato da `az
# devops`/Azure CLI): un token per questo scope, ottenuto in silenzio dalla
# STESSA sessione Azure, dà accesso alle REST API di dev.azure.com.
_AZURE_DEVOPS_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"

# Nome della chiave in `user_secrets` dove salviamo il Client ID dell'OAuth App
# GitHub dell'utente (BYOK). Non è un segreto in senso stretto — GitHub lo
# espone negli URL di login — ma lo teniamo lì per coerenza con le altre
# credenziali per-utente e per non farlo rimbalzare nelle risposte di /api/me.
GITHUB_CLIENT_ID_SECRET = "github_oauth_client_id"


def set_current_user(user_id: Any) -> None:
    """Segnala quale utente è "attivo" per il THREAD CORRENTE.

    Va chiamata all'inizio del thread dedicato che esegue davvero i tool per un
    turno (non nel thread della richiesta HTTP, che è diverso e potrebbe già
    essere passato a servire un'altra richiesta): vedi il commento di modulo.
    """
    _local.user_id = user_id


def clear_current_user() -> None:
    _local.user_id = None


# --------------------------------------------------------------------------- Azure


class AzureUserCredential:
    """Adapter `TokenCredential` (protocollo azure-core) sostenuto da una MSAL
    PublicClientApplication PER-UTENTE. Il cache dei token resta in memoria
    (comportamento di default di MSAL): nessuna persistenza su disco.
    """

    def __init__(self, app: Any, account: dict[str, Any] | None) -> None:
        self._app = app
        self._account = account

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        from azure.core.credentials import AccessToken

        result = self._app.acquire_token_silent(list(scopes), account=self._account)
        if not result or "access_token" not in result:
            # Non riapriamo mai un popup interattivo a metà di una chiamata tool:
            # se il consenso silenzioso fallisce (scope nuovo, sessione scaduta),
            # l'utente deve ri-cliccare "Connetti" — coerente col human-in-the-loop.
            raise RuntimeError(
                "Sessione Azure scaduta o consenso mancante per questo scope. "
                "Ricollega Azure dal pulsante 'Connetti' nella UI."
            )
        expires_on = int(time.time()) + int(result.get("expires_in", 3600))
        return AccessToken(result["access_token"], expires_on)


def _msal_app(tenant: str | None) -> Any:
    import msal

    client_id = os.environ.get("AZURE_OAUTH_CLIENT_ID", _AZURE_CLI_CLIENT_ID)
    authority = (
        f"https://login.microsoftonline.com/{tenant}"
        if tenant
        else "https://login.microsoftonline.com/organizations"
    )
    return msal.PublicClientApplication(client_id, authority=authority)


def start_azure_connect(user_id: Any, tenant: str | None = None) -> dict[str, Any]:
    """Avvia il Device Authorization Flow di Azure (MSAL).

    A differenza del vecchio `acquire_token_interactive` (browser + listener
    locale sulla STESSA macchina del processo — rotto su un backend hostato),
    il Device Flow chiede solo che l'UTENTE apra `verification_uri` nel SUO
    browser e digiti `user_code`: nessun requisito sulla topologia di rete tra
    backend e client, funziona identico in locale e su `portal.hemmy.it`.

    `initiate_device_flow` (una singola chiamata leggera) gira nel thread della
    richiesta e ritorna subito `user_code`/`verification_uri` (stessa UX di
    `start_github_connect`); solo il polling bloccante successivo gira in un
    thread separato.
    """
    app = _msal_app(tenant)
    flow = app.initiate_device_flow(scopes=[_ARM_SCOPE])
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description", "avvio device flow fallito"))

    with _LOCK:
        _AZURE_PENDING[user_id] = {
            "status": "waiting",
            "user_code": flow["user_code"],
            "verification_uri": flow.get("verification_uri", "https://microsoft.com/devicelogin"),
        }

    def _worker() -> None:
        try:
            # Blocca QUESTO thread (non la richiesta web) facendo polling secondo
            # l'intervallo indicato dal flow, finché l'utente completa il consenso.
            result = app.acquire_token_by_device_flow(flow)
            if not result or "access_token" not in result:
                err = (result or {}).get("error_description", "login annullato o fallito")
                raise RuntimeError(err)
            accounts = app.get_accounts()
            account = accounts[0] if accounts else None
            username = account.get("username", "connesso") if account else "connesso"
            with _LOCK:
                _AZURE_SESSIONS[user_id] = AzureUserCredential(app, account)
                _AZURE_PENDING[user_id] = {"status": "connected", "message": username}
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _AZURE_PENDING[user_id] = {
                    "status": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                }

    threading.Thread(target=_worker, daemon=True).start()

    return {
        "user_code": flow["user_code"],
        "verification_uri": flow.get("verification_uri", "https://microsoft.com/devicelogin"),
        "expires_in": flow.get("expires_in", 900),
    }


def azure_status(user_id: Any) -> dict[str, Any]:
    with _LOCK:
        connected = user_id in _AZURE_SESSIONS
        pending = dict(_AZURE_PENDING.get(user_id) or {})
    status = pending.get("status") or ("connected" if connected else "disconnected")
    out = {"connected": connected, "status": status, "message": pending.get("message")}
    if status == "waiting":
        out["user_code"] = pending.get("user_code")
        out["verification_uri"] = pending.get("verification_uri")
    return out


def get_azure_credential(user_id: Any) -> Any:
    with _LOCK:
        return _AZURE_SESSIONS.get(user_id)


def get_current_azure_credential() -> Any:
    """Usata da `infra.clients._credential()`: credenziale dell'utente attivo
    nel THREAD CORRENTE, se ha completato il login OAuth per-utente."""
    uid = getattr(_local, "user_id", None)
    if uid is None:
        return None
    return get_azure_credential(uid)


def disconnect_azure(user_id: Any) -> None:
    with _LOCK:
        _AZURE_SESSIONS.pop(user_id, None)
        _AZURE_PENDING.pop(user_id, None)
        _AZURE_DEVOPS_ORGS.pop(user_id, None)


# ------------------------------------------------------------------- Azure DevOps


def connect_azure_devops(user_id: Any, organization: str) -> dict[str, Any]:
    """Registra l'organization Azure DevOps per l'utente (NON un login separato:
    riusa la sessione Azure già connessa). Valida subito che il token per lo
    scope Azure DevOps si possa ottenere in silenzio dalla stessa sessione."""
    org = (organization or "").strip()
    org = org.replace("https://dev.azure.com/", "").replace("http://", "").strip("/")
    if not org:
        raise RuntimeError("Nome della organization Azure DevOps obbligatorio.")

    cred = get_azure_credential(user_id)
    if cred is None:
        raise RuntimeError(
            "Connetti prima Azure (stesso account Microsoft): Azure DevOps "
            "riusa quella sessione, non richiede un login separato."
        )

    with _LOCK:
        _AZURE_DEVOPS_ORGS[user_id] = org

    # Verifica subito che il tenant conceda lo scope Azure DevOps allo stesso
    # client — se fallisce, meglio saperlo ora che al primo uso di un tool.
    try:
        _azure_devops_token(user_id)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _AZURE_DEVOPS_ORGS.pop(user_id, None)
        raise RuntimeError(
            "Sessione Azure valida ma il tenant non consente l'accesso ad Azure "
            f"DevOps per questo client (serve consenso admin): {exc}"
        ) from exc

    return {"organization": org}


def _azure_devops_token(user_id: Any) -> str:
    cred = get_azure_credential(user_id)
    if cred is None:
        raise RuntimeError("Azure non connesso.")
    token = cred._app.acquire_token_silent([_AZURE_DEVOPS_SCOPE], account=cred._account)
    if not token or "access_token" not in token:
        raise RuntimeError(
            "Sessione Azure DevOps scaduta o consenso mancante. Ricollega Azure "
            "dal pulsante 'Connetti' nella UI."
        )
    return token["access_token"]


def azure_devops_status(user_id: Any) -> dict[str, Any]:
    with _LOCK:
        org = _AZURE_DEVOPS_ORGS.get(user_id)
    if not org:
        return {"connected": False, "status": "disconnected"}
    if get_azure_credential(user_id) is None:
        return {"connected": False, "status": "disconnected", "message": "riconnetti Azure"}
    return {"connected": True, "status": "connected", "message": org, "organization": org}


def get_azure_devops_credential(user_id: Any) -> dict[str, Any] | None:
    """`{"organization": str, "token": str}` pronto per le REST API di
    `dev.azure.com/<organization>`, o None se non connesso. Il token è
    riacquisito ad ogni chiamata (silenzioso, dalla cache MSAL della sessione
    Azure): sempre fresco, nessuna cache separata da gestire qui."""
    with _LOCK:
        org = _AZURE_DEVOPS_ORGS.get(user_id)
    if not org:
        return None
    try:
        token = _azure_devops_token(user_id)
    except Exception:  # noqa: BLE001
        return None
    return {"organization": org, "token": token}


def get_current_azure_devops_credential() -> dict[str, Any] | None:
    """Usata dai futuri tool `azdo.*`: credenziale dell'utente attivo nel
    THREAD CORRENTE (stesso schema di `get_current_azure_credential`)."""
    uid = getattr(_local, "user_id", None)
    if uid is None:
        return None
    return get_azure_devops_credential(uid)


def disconnect_azure_devops(user_id: Any) -> None:
    with _LOCK:
        _AZURE_DEVOPS_ORGS.pop(user_id, None)


# -------------------------------------------------------------------------- GitHub


def _http_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {exc.code}: {raw[:300]}") from None


def _fetch_github_login(token: str) -> str:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("login", "connesso")


# ------------------------------------------------------------------ GitHub BYOK
# Il Client ID dell'OAuth App GitHub è per-utente, salvato in `user_secrets`
# con la chiave `GITHUB_CLIENT_ID_SECRET`. Un fallback opzionale all'env var
# `GITHUB_OAUTH_CLIENT_ID` permette di offrire un default di piattaforma per
# chi non ha configurato il proprio; se assente, l'utente DEVE configurare il
# suo (la UI mostra il pulsante "Configura OAuth App" in quel caso).


def save_github_oauth_client_id(users: Any, user_id: Any, client_id: str) -> None:
    """Salva il Client ID dell'OAuth App GitHub dell'utente.

    Non è un segreto in senso stretto (GitHub lo espone negli URL di login),
    ma lo teniamo in `user_secrets` per coerenza con le altre credenziali
    per-utente e per non farlo rimbalzare nelle risposte di /api/me.
    """
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("Client ID obbligatorio")
    users.set_secret(user_id, GITHUB_CLIENT_ID_SECRET, cid)


def get_github_oauth_client_id(users: Any, user_id: Any) -> str | None:
    """Client ID dell'utente, con fallback opzionale all'env var di piattaforma.

    Ordine di ricerca:
      1. `user_secrets["github_oauth_client_id"]` dell'utente (BYOK).
      2. `GITHUB_OAUTH_CLIENT_ID` (default di piattaforma, se configurato).

    Ritorna `None` se nessuna delle due è presente: in quel caso la UI deve
    invitare l'utente a configurare la propria OAuth App.
    """
    try:
        cid = users.get_secret(user_id, GITHUB_CLIENT_ID_SECRET)
    except Exception:  # noqa: BLE001
        cid = None
    if cid and cid.strip():
        return cid.strip()
    env = (os.environ.get("GITHUB_OAUTH_CLIENT_ID") or "").strip()
    return env or None


def is_github_oauth_configured(users: Any, user_id: Any) -> bool:
    """True se l'utente ha configurato il proprio Client ID (BYOK) o se è
    disponibile un default di piattaforma via env var.

    Usata dalla UI per mostrare subito lo stato reale invece di far cliccare
    "Connetti" e mostrare un errore.
    """
    return bool(get_github_oauth_client_id(users, user_id))


def start_github_connect(
    users: Any, user_id: Any, scope: str = "repo workflow"
) -> dict[str, Any]:
    """Avvia il Device Authorization Flow di GitHub con il Client ID dell'utente.

    Ritorna subito `user_code` + `verification_uri`: l'utente deve aprirli lui
    (GitHub non permette un redirect automatico per il device flow) e concedere
    il consenso col proprio account. Il polling per lo scambio del token avviene
    in un thread separato.

    Il Client ID viene letto da `user_secrets` (BYOK): ogni utente crea la
    propria OAuth App e la salva in Hemmy. Fallback opzionale all'env var
    `GITHUB_OAUTH_CLIENT_ID` se configurata a livello di piattaforma.
    """
    client_id = get_github_oauth_client_id(users, user_id)
    if not client_id:
        raise RuntimeError(
            "GitHub OAuth non configurato: crea una OAuth App su "
            "github.com/settings/developers con 'Device Flow' abilitato "
            "(nessun client secret richiesto) e incolla il Client ID nelle "
            "impostazioni del tuo profilo Hemmy."
        )
    data = _http_json(
        "https://github.com/login/device/code", {"client_id": client_id, "scope": scope}
    )
    if "device_code" not in data:
        raise RuntimeError(data.get("error_description", "avvio device flow fallito"))

    with _LOCK:
        _GITHUB_PENDING[user_id] = {
            "status": "waiting",
            "user_code": data["user_code"],
            "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
        }

    threading.Thread(
        target=_poll_github_device, args=(user_id, client_id, data), daemon=True
    ).start()

    return {
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
        "expires_in": data.get("expires_in", 900),
    }


def _poll_github_device(user_id: Any, client_id: str, device_data: dict[str, Any]) -> None:
    interval = int(device_data.get("interval", 5))
    device_code = device_data["device_code"]
    deadline = time.time() + int(device_data.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        try:
            data = _http_json(
                "https://github.com/login/oauth/access_token",
                {
                    "client_id": client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _GITHUB_PENDING[user_id] = {"status": "error", "message": str(exc)}
            return
        if data.get("access_token"):
            token = data["access_token"]
            try:
                login = _fetch_github_login(token)
            except Exception:  # noqa: BLE001
                login = "connesso"
            with _LOCK:
                _GITHUB_SESSIONS[user_id] = {"token": token, "login": login}
                _GITHUB_PENDING[user_id] = {"status": "connected", "login": login}
            return
        err = data.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        with _LOCK:
            _GITHUB_PENDING[user_id] = {
                "status": "error",
                "message": data.get("error_description", err or "errore sconosciuto"),
            }
        return
    with _LOCK:
        _GITHUB_PENDING[user_id] = {"status": "error", "message": "tempo scaduto, riprova"}


def github_status(user_id: Any) -> dict[str, Any]:
    with _LOCK:
        connected = user_id in _GITHUB_SESSIONS
        session = dict(_GITHUB_SESSIONS.get(user_id) or {})
        pending = dict(_GITHUB_PENDING.get(user_id) or {})
    status = pending.get("status") or ("connected" if connected else "disconnected")
    out = {"connected": connected, "status": status, "login": session.get("login")}
    if status == "waiting":
        out["user_code"] = pending.get("user_code")
        out["verification_uri"] = pending.get("verification_uri")
    if status == "error":
        out["message"] = pending.get("message")
    return out


def get_github_token(user_id: Any) -> str | None:
    with _LOCK:
        session = _GITHUB_SESSIONS.get(user_id)
        return session.get("token") if session else None


def get_current_github_token() -> str | None:
    """Usata da `tools.cicd.cicd_tools.resolve_github_token()`: token dell'utente
    attivo nel THREAD CORRENTE, se ha completato il login OAuth per-utente."""
    uid = getattr(_local, "user_id", None)
    if uid is None:
        return None
    return get_github_token(uid)


def disconnect_github(user_id: Any) -> None:
    with _LOCK:
        _GITHUB_SESSIONS.pop(user_id, None)
        _GITHUB_PENDING.pop(user_id, None)


# ----------------------------------------------------------------------------- GCP

_GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
# Scope ampio per l'accesso alle risorse GCP, equivalente del `user_impersonation`
# ARM per Azure. Tool GCP futuri più mirati potranno restringerlo se necessario.
_GCP_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def is_gcp_oauth_configured() -> bool:
    """True se sia client id CHE client secret Google sono impostati: a
    differenza di GitHub/Azure, Google richiede il secret anche per un client
    "TV and Limited-Input Device" (particolarità di Google, non di questo
    progetto) — resta comunque solo lato backend, mai esposto al frontend."""
    return bool(
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    )


def _fetch_gcp_email(access_token: str) -> str:
    req = urllib.request.Request(
        _GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("email", "connesso")


def start_gcp_connect(user_id: Any) -> dict[str, Any]:
    """Avvia il Device Authorization Flow di Google. Stesso pattern di
    `start_github_connect`: l'utente apre `verification_uri`, inserisce
    `user_code`, il polling avviene in un thread separato."""
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET non configurati. "
            "Crea in Google Cloud Console → API e servizi → Credenziali → "
            "'ID client OAuth' di tipo 'TV e dispositivi con input limitato', "
            "e imposta le due variabili nel backend."
        )
    data = _http_json(_GOOGLE_DEVICE_CODE_URL, {"client_id": client_id, "scope": _GCP_SCOPE})
    if "device_code" not in data:
        raise RuntimeError(data.get("error_description", "avvio device flow fallito"))

    with _LOCK:
        _GCP_PENDING[user_id] = {
            "status": "waiting",
            "user_code": data["user_code"],
            "verification_uri": data.get("verification_url", "https://www.google.com/device"),
        }

    threading.Thread(
        target=_poll_gcp_device, args=(user_id, client_id, client_secret, data), daemon=True
    ).start()

    return {
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_url", "https://www.google.com/device"),
        "expires_in": data.get("expires_in", 1800),
    }


def _poll_gcp_device(user_id: Any, client_id: str, client_secret: str, device_data: dict[str, Any]) -> None:
    interval = int(device_data.get("interval", 5))
    device_code = device_data["device_code"]
    deadline = time.time() + int(device_data.get("expires_in", 1800))
    while time.time() < deadline:
        time.sleep(interval)
        try:
            data = _http_json(
                _GOOGLE_TOKEN_URL,
                {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _GCP_PENDING[user_id] = {"status": "error", "message": str(exc)}
            return
        if data.get("access_token"):
            access_token = data["access_token"]
            refresh_token = data.get("refresh_token")
            expires_at = time.time() + int(data.get("expires_in", 3600))
            try:
                email = _fetch_gcp_email(access_token)
            except Exception:  # noqa: BLE001
                email = "connesso"
            with _LOCK:
                _GCP_SESSIONS[user_id] = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": expires_at,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "email": email,
                }
                _GCP_PENDING[user_id] = {"status": "connected", "email": email}
            return
        err = data.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        with _LOCK:
            _GCP_PENDING[user_id] = {
                "status": "error",
                "message": data.get("error_description", err or "errore sconosciuto"),
            }
        return
    with _LOCK:
        _GCP_PENDING[user_id] = {"status": "error", "message": "tempo scaduto, riprova"}


def gcp_status(user_id: Any) -> dict[str, Any]:
    with _LOCK:
        connected = user_id in _GCP_SESSIONS
        session = dict(_GCP_SESSIONS.get(user_id) or {})
        pending = dict(_GCP_PENDING.get(user_id) or {})
    status = pending.get("status") or ("connected" if connected else "disconnected")
    out = {"connected": connected, "status": status, "message": session.get("email")}
    if status == "waiting":
        out["user_code"] = pending.get("user_code")
        out["verification_uri"] = pending.get("verification_uri")
    if status == "error":
        out["message"] = pending.get("message")
    return out


def get_gcp_access_token(user_id: Any) -> str | None:
    """Access token GCP valido, rinnovato in silenzio via refresh_token se
    prossimo alla scadenza (stesso principio di `acquire_token_silent` MSAL,
    fatto a mano perché qui non c'è una libreria che lo gestisca per noi)."""
    with _LOCK:
        session = dict(_GCP_SESSIONS.get(user_id) or {})
    if not session:
        return None
    if time.time() < session["expires_at"] - 60:
        return session["access_token"]
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return None  # nessun refresh possibile: l'utente deve riconnettersi
    try:
        data = _http_json(
            _GOOGLE_TOKEN_URL,
            {
                "client_id": session["client_id"],
                "client_secret": session["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except Exception:  # noqa: BLE001
        return None
    if "access_token" not in data:
        return None
    with _LOCK:
        cur = _GCP_SESSIONS.get(user_id)
        if cur is not None:
            cur["access_token"] = data["access_token"]
            cur["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return data["access_token"]


def get_current_gcp_access_token() -> str | None:
    """Usata dai futuri tool `gcp.*`: access token dell'utente attivo nel
    THREAD CORRENTE (stesso schema di `get_current_azure_credential`)."""
    uid = getattr(_local, "user_id", None)
    if uid is None:
        return None
    return get_gcp_access_token(uid)


def disconnect_gcp_oauth(user_id: Any) -> None:
    with _LOCK:
        _GCP_SESSIONS.pop(user_id, None)
        _GCP_PENDING.pop(user_id, None)


# ----------------------------------------------------------------------------- AWS


def is_aws_sso_configured() -> bool:
    """A differenza degli altri provider, AWS SSO non ha un client id fisso
    dell'agente da configurare: ogni organizzazione ha il proprio Identity
    Center. Il flusso è quindi "sempre disponibile" nel senso che non richiede
    variabili d'ambiente — ma richiede che l'utente conosca l'URL del portale
    SSO della SUA organizzazione."""
    return True


def start_aws_sso_connect(user_id: Any, start_url: str, region: str = "eu-west-1") -> dict[str, Any]:
    """Avvia il Device Authorization Flow di AWS IAM Identity Center (SSO).

    A differenza di Azure/GitHub/GCP (dove il client OAuth è dell'AGENTE),
    qui `start_url` è il portale SSO della ORGANIZZAZIONE del cliente (es.
    `https://azienda.awsapps.com/start`): se l'organizzazione non ha Identity
    Center attivo, questo flusso non è utilizzabile — l'inserimento di chiavi
    statiche (`auth/cloud_credentials.py`) resta disponibile come fallback
    esplicito per quel caso, non come default silenzioso.
    """
    import boto3

    start_url = (start_url or "").strip()
    region = (region or "eu-west-1").strip()
    if not start_url:
        raise RuntimeError("URL del portale SSO (start URL) obbligatorio.")

    # RegisterClient + StartDeviceAuthorization sono due singole chiamate
    # leggere: girano nel thread della richiesta (stessa UX immediata di
    # `start_github_connect`); solo il polling bloccante va in un thread a parte.
    oidc = boto3.client("sso-oidc", region_name=region)
    reg = oidc.register_client(clientName="hemmy-agent", clientType="public")
    client_id = reg["clientId"]
    client_secret = reg["clientSecret"]

    auth = oidc.start_device_authorization(
        clientId=client_id, clientSecret=client_secret, startUrl=start_url
    )
    verification_uri = auth.get("verificationUriComplete") or auth["verificationUri"]

    with _LOCK:
        _AWS_PENDING[user_id] = {
            "status": "waiting",
            "user_code": auth["userCode"],
            "verification_uri": verification_uri,
        }

    threading.Thread(
        target=_aws_sso_poll_worker,
        args=(user_id, oidc, client_id, client_secret, auth, region),
        daemon=True,
    ).start()

    return {
        "user_code": auth["userCode"],
        "verification_uri": verification_uri,
        "expires_in": auth.get("expiresIn", 600),
    }


def _aws_sso_poll_worker(
    user_id: Any, oidc: Any, client_id: str, client_secret: str, auth: dict[str, Any], region: str
) -> None:
    import boto3

    try:
        interval = int(auth.get("interval", 5))
        deadline = time.time() + int(auth.get("expiresIn", 600))
        device_code = auth["deviceCode"]
        access_token = None
        while time.time() < deadline:
            time.sleep(interval)
            try:
                tok = oidc.create_token(
                    clientId=client_id,
                    clientSecret=client_secret,
                    grantType="urn:ietf:params:oauth:grant-type:device_code",
                    deviceCode=device_code,
                )
                access_token = tok["accessToken"]
                break
            except oidc.exceptions.AuthorizationPendingException:
                continue
            except oidc.exceptions.SlowDownException:
                interval += 5
                continue
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    _AWS_PENDING[user_id] = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                return
        if not access_token:
            with _LOCK:
                _AWS_PENDING[user_id] = {"status": "error", "message": "tempo scaduto, riprova"}
            return

        with _LOCK:
            _AWS_SSO_SESSIONS[user_id] = {"access_token": access_token, "region": region}

        # Elenca gli account/permission-set assegnati a questo utente in Identity
        # Center: se ce n'è uno solo, finalizza subito; altrimenti l'utente deve
        # scegliere (vedi `select_aws_role`).
        sso = boto3.client("sso", region_name=region)
        options: list[dict[str, str]] = []
        next_token = None
        while True:
            kw: dict[str, Any] = {"accessToken": access_token}
            if next_token:
                kw["nextToken"] = next_token
            resp = sso.list_accounts(**kw)
            for acc in resp.get("accountList", []):
                roles_resp = sso.list_account_roles(accessToken=access_token, accountId=acc["accountId"])
                for role in roles_resp.get("roleList", []):
                    options.append({
                        "account_id": acc["accountId"],
                        "account_name": acc.get("accountName", acc["accountId"]),
                        "role_name": role["roleName"],
                    })
            next_token = resp.get("nextToken")
            if not next_token:
                break

        if not options:
            with _LOCK:
                _AWS_PENDING[user_id] = {
                    "status": "error",
                    "message": "nessun account/ruolo assegnato a questo utente in Identity Center",
                }
            return

        if len(options) == 1:
            _finalize_aws_role(user_id, options[0]["account_id"], options[0]["role_name"])
            return

        with _LOCK:
            _AWS_PENDING[user_id] = {"status": "select_role", "options": options}
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _AWS_PENDING[user_id] = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


def select_aws_role(user_id: Any, account_id: str, role_name: str) -> None:
    """Completa la connessione AWS scegliendo l'account/ruolo tra quelli
    proposti — necessario solo se l'utente ha più di un account/permission-set
    assegnato in Identity Center (vedi lo stato `select_role`)."""
    if not get_aws_sso_session(user_id):
        raise RuntimeError("Nessuna sessione SSO in corso: avvia prima la connessione AWS.")
    _finalize_aws_role(user_id, account_id, role_name)


def get_aws_sso_session(user_id: Any) -> dict[str, Any] | None:
    with _LOCK:
        return dict(_AWS_SSO_SESSIONS[user_id]) if user_id in _AWS_SSO_SESSIONS else None


def _finalize_aws_role(user_id: Any, account_id: str, role_name: str) -> None:
    import boto3

    session = get_aws_sso_session(user_id)
    if not session:
        with _LOCK:
            _AWS_PENDING[user_id] = {"status": "error", "message": "sessione SSO scaduta, riconnetti"}
        return
    try:
        sso = boto3.client("sso", region_name=session["region"])
        resp = sso.get_role_credentials(
            accessToken=session["access_token"], accountId=account_id, roleName=role_name
        )
        rc = resp["roleCredentials"]
        with _LOCK:
            _AWS_ROLE_CREDS[user_id] = {
                "account_id": account_id,
                "role_name": role_name,
                "access_key_id": rc["accessKeyId"],
                "secret_access_key": rc["secretAccessKey"],
                "session_token": rc["sessionToken"],
                "expiration": rc["expiration"],  # epoch millis
                "region": session["region"],
            }
            _AWS_PENDING[user_id] = {"status": "connected", "message": f"{account_id} / {role_name}"}
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _AWS_PENDING[user_id] = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


def aws_sso_status(user_id: Any) -> dict[str, Any]:
    with _LOCK:
        connected = user_id in _AWS_ROLE_CREDS
        creds = dict(_AWS_ROLE_CREDS.get(user_id) or {})
        pending = dict(_AWS_PENDING.get(user_id) or {})
    status = pending.get("status") or ("connected" if connected else "disconnected")
    out: dict[str, Any] = {"connected": connected, "status": status}
    if status == "waiting":
        out["user_code"] = pending.get("user_code")
        out["verification_uri"] = pending.get("verification_uri")
    if status == "select_role":
        out["options"] = pending.get("options")
    if status == "error":
        out["message"] = pending.get("message")
    if connected:
        out["message"] = f"{creds.get('account_id')} / {creds.get('role_name')}"
    return out


def get_aws_sso_credentials(user_id: Any) -> dict[str, Any] | None:
    """Credenziali AWS temporanee (STS) da Identity Center, rinnovate in
    automatico (stesso account/ruolo) quando prossime alla scadenza, finché il
    token SSO OIDC in memoria resta valido."""
    with _LOCK:
        creds = dict(_AWS_ROLE_CREDS.get(user_id) or {})
    if not creds:
        return None
    if time.time() * 1000 < creds.get("expiration", 0) - 60_000:
        return creds
    _finalize_aws_role(user_id, creds["account_id"], creds["role_name"])
    with _LOCK:
        refreshed = dict(_AWS_ROLE_CREDS.get(user_id) or {})
    return refreshed or None


def get_current_aws_sso_credentials() -> dict[str, Any] | None:
    """Usata dai futuri tool `aws.*`: credenziali dell'utente attivo nel THREAD
    CORRENTE (stesso schema di `get_current_azure_credential`)."""
    uid = getattr(_local, "user_id", None)
    if uid is None:
        return None
    return get_aws_sso_credentials(uid)


def disconnect_aws_sso(user_id: Any) -> None:
    with _LOCK:
        _AWS_SSO_SESSIONS.pop(user_id, None)
        _AWS_ROLE_CREDS.pop(user_id, None)
        _AWS_PENDING.pop(user_id, None)