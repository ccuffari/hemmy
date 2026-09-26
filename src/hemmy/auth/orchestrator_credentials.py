"""Credenziali per orchestratori di workflow (attualmente: Airflow).

A differenza dei provider cloud/DevOps (che usano OAuth quando possibile),
gli orchestratori come Airflow sono tipicamente self-hosted o gestiti da
servizi come Astronomer/Conveyor, e l'autenticazione più comune è:

- **Basic Auth** (username + password) — default di Airflow 2.x self-hosted
- **Bearer Token** (API key / JWT) — Airflow 3.x, Astronomer, MWAA con token

Per questo salviamo le credenziali **cifrate a riposo** in `user_secrets`
come per AWS/GCP/AzDO, con un test di validità immediato al salvataggio.

Convenzione nomi segreti:
    airflow_base_url       es. https://airflow.mycompany.com
    airflow_auth_mode      "basic" | "token"
    airflow_username       (solo basic)
    airflow_password       (solo basic)
    airflow_token          (solo token)
    airflow_verify_ssl     "1" | "0"  (default "1")
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


AIRFLOW_SECRET_NAMES = (
    "airflow_base_url",
    "airflow_auth_mode",
    "airflow_username",
    "airflow_password",
    "airflow_token",
    "airflow_verify_ssl",
)


def _normalize_base_url(url: str) -> str:
    """Rimuove slash finale e spazi, aggiunge https:// se manca lo schema."""
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def _build_request(base_url: str, path: str, mode: str,
                   username: str, password: str, token: str) -> urllib.request.Request:
    """Costruisce una Request con l'header Authorization appropriato."""
    url = base_url + path
    headers = {"Accept": "application/json"}
    if mode == "basic":
        import base64
        raw = f"{username}:{password}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif mode == "token":
        headers["Authorization"] = "Bearer " + token
    return urllib.request.Request(url, headers=headers, method="GET")


def _test_airflow_connection(base_url: str, mode: str, username: str,
                             password: str, token: str, verify_ssl: bool) -> dict[str, Any]:
    """Prova un endpoint di health. Ritorna {ok, version, status, error}.

    Prova `/api/v1/health` (Airflow 2.x) e in fallback `/api/v2/health` (3.x).
    """
    import ssl

    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    last_err: str | None = None
    for path in ("/api/v1/health", "/api/v2/health", "/health"):
        try:
            req = _build_request(base_url, path, mode, username, password, token)
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
                return {
                    "ok": True,
                    "endpoint": path,
                    "metadatabase": data.get("metadatabase"),
                    "scheduler": data.get("scheduler"),
                    "triggerer": data.get("triggerer"),
                    "raw": data,
                }
        except urllib.error.HTTPError as exc:
            # 401/403 → credenziali sbagliate: non ha senso provare altri path
            if exc.code in (401, 403):
                return {
                    "ok": False,
                    "status": exc.code,
                    "error": "Credenziali rifiutate dal server Airflow (HTTP "
                             f"{exc.code}).",
                }
            last_err = f"HTTP {exc.code} su {path}"
            continue
        except urllib.error.URLError as exc:
            last_err = f"rete: {exc.reason}"
            continue
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            continue

    return {"ok": False, "error": last_err or "endpoint di health non raggiungibile"}


# ------------------------------------------------------------------ Save / Load

def save_airflow_credentials(users: Any, user_id: Any, *,
                             base_url: str,
                             auth_mode: str,
                             username: str = "",
                             password: str = "",
                             token: str = "",
                             verify_ssl: bool = True) -> dict[str, Any]:
    """Valida e salva le credenziali Airflow dell'utente.

    Ritorna {base_url, auth_mode, connected, message, endpoint} in caso di
    successo; solleva ValueError con l'errore in caso di fallimento.
    """
    base_url = _normalize_base_url(base_url)
    auth_mode = (auth_mode or "basic").strip().lower()
    if auth_mode not in ("basic", "token"):
        raise ValueError("auth_mode deve essere 'basic' o 'token'")
    if not base_url:
        raise ValueError("base_url obbligatorio")

    if auth_mode == "basic":
        if not (username or "").strip() or not (password or "").strip():
            raise ValueError("Per Basic Auth servono username e password.")
    else:  # token
        if not (token or "").strip():
            raise ValueError("Per Bearer Token serve un token.")

    # Test immediato: se fallisce, non salvo (evita di persistere credenziali rotte)
    test = _test_airflow_connection(
        base_url, auth_mode,
        (username or "").strip(), password or "", (token or "").strip(),
        verify_ssl,
    )
    if not test.get("ok"):
        raise ValueError(
            f"Connessione Airflow fallita: {test.get('error', 'errore sconosciuto')}"
        )

    # Persistenza cifrata (set_secret cifra già)
    users.set_secret(user_id, "airflow_base_url", base_url)
    users.set_secret(user_id, "airflow_auth_mode", auth_mode)
    users.set_secret(user_id, "airflow_verify_ssl", "1" if verify_ssl else "0")
    # Pulisco prima i campi dell'altra modalità, per evitare residui
    for n in ("airflow_username", "airflow_password", "airflow_token"):
        try:
            users.delete_secret(user_id, n)
        except Exception:  # noqa: BLE001
            pass
    if auth_mode == "basic":
        users.set_secret(user_id, "airflow_username", (username or "").strip())
        users.set_secret(user_id, "airflow_password", password or "")
    else:
        users.set_secret(user_id, "airflow_token", (token or "").strip())

    # Salva anche un campo non-segreto in settings (per UI) con endpoint testato
    try:
        settings = users.get_settings(user_id) or {}
        orch = settings.get("orchestrators") or {}
        orch["airflow"] = {
            "base_url": base_url,
            "auth_mode": auth_mode,
            "endpoint": test.get("endpoint", "/api/v1/health"),
        }
        users.set_settings(user_id, {"orchestrators": orch})
    except Exception:  # noqa: BLE001
        pass

    return {
        "base_url": base_url,
        "auth_mode": auth_mode,
        "connected": True,
        "endpoint": test.get("endpoint"),
        "message": "Airflow " + (test.get("endpoint") or ""),
    }


def load_airflow_credentials(users: Any, user_id: Any) -> dict[str, Any] | None:
    """Ritorna {base_url, auth_mode, username, password, token, verify_ssl} o None."""
    try:
        base_url = users.get_secret(user_id, "airflow_base_url")
        auth_mode = users.get_secret(user_id, "airflow_auth_mode")
    except Exception:  # noqa: BLE001
        return None
    if not base_url or not auth_mode:
        return None
    out: dict[str, Any] = {
        "base_url": base_url,
        "auth_mode": auth_mode,
        "verify_ssl": True,
    }
    try:
        vs = users.get_secret(user_id, "airflow_verify_ssl")
        out["verify_ssl"] = (vs != "0")
    except Exception:  # noqa: BLE001
        pass
    if auth_mode == "basic":
        try:
            out["username"] = users.get_secret(user_id, "airflow_username") or ""
            out["password"] = users.get_secret(user_id, "airflow_password") or ""
        except Exception:  # noqa: BLE001
            return None
    else:
        try:
            out["token"] = users.get_secret(user_id, "airflow_token") or ""
        except Exception:  # noqa: BLE001
            return None
    return out


def airflow_status(users: Any, user_id: Any) -> dict[str, Any]:
    """Stato corrente per la UI (senza segreti)."""
    creds = load_airflow_credentials(users, user_id)
    if not creds:
        return {"connected": False, "status": "disconnected"}

    # Per non fare una chiamata a ogni polling, il "connected" è basato sulla
    # presenza delle credenziali: il test reale è avvenuto al salvataggio.
    settings = {}
    try:
        settings = users.get_settings(user_id) or {}
    except Exception:  # noqa: BLE001
        pass
    orch = (settings.get("orchestrators") or {}).get("airflow") or {}

    return {
        "connected": True,
        "status": "connected",
        "base_url": creds["base_url"],
        "auth_mode": creds["auth_mode"],
        "message": creds["base_url"],
        "endpoint": orch.get("endpoint"),
    }


def disconnect_airflow(users: Any, user_id: Any) -> None:
    """Rimuove tutti i segreti e ripulisce settings."""
    for n in AIRFLOW_SECRET_NAMES:
        try:
            users.delete_secret(user_id, n)
        except Exception:  # noqa: BLE001
            pass
    try:
        settings = users.get_settings(user_id) or {}
        orch = settings.get("orchestrators") or {}
        orch.pop("airflow", None)
        users.set_settings(user_id, {"orchestrators": orch})
    except Exception:  # noqa: BLE001
        pass