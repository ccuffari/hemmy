"""Interfaccia web di Hemmy (FastAPI + browser).

Autenticazione: Supabase Auth (JWT verificati con JWKS pubblico).
BYOK: le API key LLM dell'utente sono salvate cifrate in `user_secrets`.
Multi-provider LLM con fallback, cloud credentials, Airflow, GitHub BYOK.
Stop dell'elaborazione, approvazioni con timeout, SSE, CORS, logging.

Admin:
  - GET  /api/admin/native-tools
  - POST /api/admin/promote-plugin
  - POST /api/admin/review-plugin   (approva / rifiuta / rimette in review)

Avvio:
  - Locale:  python -m hemmy --web   -> http://127.0.0.1:8765
  - Cloud Run: legge HOST/PORT dall'ambiente.
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from typing import Any

from hemmy.interfaces import cli as _cli
from hemmy.auth.supabase_jwt import (
    extract_token as _jwt_extract_token,
    verify_supabase_jwt,
)
from hemmy.auth.cloud_credentials import (
    aws_status as _aws_static_status,
    disconnect_aws,
    save_aws_credentials,
)
from hemmy.auth.orchestrator_credentials import (
    airflow_status as _airflow_status,
    disconnect_airflow,
    save_airflow_credentials,
)
from hemmy.logging_config import setup_logging, get_logger, timed

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError as _exc:  # pragma: no cover - dipendenza opzionale al modulo
    raise SystemExit(
        "FastAPI non installato. Esegui: pip install fastapi uvicorn"
    ) from _exc
# NOTA IMPORTANTE: `Request` deve essere importato qui, a livello di MODULO (non
# dentro `create_app()`), perché questo file ha `from __future__ import
# annotations` in cima: tutte le annotazioni dei parametri (es. `request:
# Request` nelle route) diventano stringhe a runtime, e FastAPI le risolve
# con `typing.get_type_hints()` guardando i GLOBALS del modulo — non le
# closure locali. Se `Request` esiste solo dentro `create_app()`, FastAPI non
# riesce a risolvere l'annotazione, non riconosce il parametro come "oggetto
# Request iniettato dal framework" e lo tratta come un query param normale di
# nome `request` → 422 "Field required" su OGNI endpoint che lo usa (bug
# osservato con fastapi>=0.14x/starlette>=1.x dopo l'upgrade delle dipendenze).


setup_logging()
log = get_logger("web")
log_turn = get_logger("turn")
log_llm = get_logger("llm")
log_session = get_logger("session")


# --------------------------------------------------------------------------- Utils

def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


_LLM_AUTH_PATTERNS = (
    "authenticationerror",
    "invalid api key",
    "invalid_api_key",
    "invalid key",
    "unauthorized",
    "401",
    "incorrect api key",
    "invalidauthentication",
    "authentication failed",
    "insufficient_quota",
    "429",
    "ratelimiterror",
    "rate limit",
    "no api key",
    "missing api key",
    "access denied",
    "permission denied",
    "billing",
)


def _is_llm_auth_error(exc: BaseException) -> bool:
    try:
        msg = f"{type(exc).__name__}: {exc}".lower()
    except Exception:
        return False
    return any(p in msg for p in _LLM_AUTH_PATTERNS)


class _TurnCancelled(BaseException):
    """Segnala che l'utente ha richiesto lo stop del turno corrente."""
    pass


# --------------------------------------------------------------------------- Hot reload

def _hot_reload_tools(session: "_WebSession") -> None:
    """Ricostruisce l'agente dello stesso utente (plugin installati/rimossi)."""
    uid = session.user_id
    convo = list(getattr(session.agent, "conversation", []) or [])
    cfg = _llm_config_for_user(uid) if uid is not None else None
    if cfg is None:
        log.warning("hot reload tool saltato: nessuna config LLM per user=%s", uid)
        return
    session.agent = _cli._build_agent(
        approval_fn=session.approval_fn,
        secret_prompt_fn=session.secret_fn,
        user_id=uid,
        llm_config=cfg,
    )
    try:
        session.agent.load_conversation(convo)
    except Exception:
        pass
    log.info("hot reload tool completato user=%s", uid)


# --------------------------------------------------------------------------- Sessione

class _WebSession:
    """Stato di UNA sessione web, isolato per singolo utente."""

    def __init__(self) -> None:
        self.events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.answer: "queue.Queue[Any]" = queue.Queue()
        self.pending: str | None = None
        self.pending_payload: dict[str, Any] | None = None
        self.busy = False
        self.stop_requested = False
        self.agent: Any = None
        self.user_id: Any = None
        self.llm_candidates: list[dict[str, Any]] = []
        self.llm_active_idx: int = 0

    def approval_fn(self, action: str, args: dict[str, Any], preview: str | None) -> bool:
        payload = {
            "action": action,
            "args": _jsonable(args),
            "preview": preview or "",
        }
        self.pending = "approval"
        self.pending_payload = payload
        self.events.put({"type": "approval_request", **payload})
        try:
            val = self.answer.get(timeout=300)
        except queue.Empty:
            log_turn.warning(
                "approvazione scaduta per %s (nessuna risposta dal browser entro 5 min)",
                action,
                extra={"user_id": self.user_id, "phase": "approval.timeout"},
            )
            return False
        finally:
            self.pending = None
            self.pending_payload = None
        if val is True:
            return True
        if isinstance(val, str):
            return val.strip().lower() in {"si", "sì", "s", "y", "yes", "true"}
        return False

    def secret_fn(self, label: str) -> str:
        self.pending = "secret"
        self.pending_payload = {"label": label}
        self.events.put({"type": "secret_request", "label": label})
        try:
            val = self.answer.get(timeout=300)
        except queue.Empty:
            log_turn.warning(
                "input segreto scaduto (nessuna risposta dal browser entro 5 min)",
                extra={"user_id": self.user_id, "phase": "secret.timeout"},
            )
            return ""
        finally:
            self.pending = None
            self.pending_payload = None
        return val if isinstance(val, str) else ""


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return json.loads(json.dumps(obj, default=str))


_SESSIONS: dict[Any, "_WebSession"] = {}
_SESSIONS_LOCK = threading.Lock()


def _get_session(user_id: Any) -> "_WebSession":
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(user_id)
        if sess is None:
            sess = _WebSession()
            sess.user_id = user_id
            _SESSIONS[user_id] = sess
            log_session.info("sessione creata user=%s", user_id)
        return sess


def _drop_session(user_id: Any) -> None:
    with _SESSIONS_LOCK:
        if _SESSIONS.pop(user_id, None) is not None:
            log_session.info("sessione rimossa user=%s", user_id)


# --------------------------------------------------------------------------- Rate limit

_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_HITS: dict[Any, list[float]] = {}
_RATE_LIMIT_MAX_REQUESTS = 20
_RATE_LIMIT_WINDOW_SECONDS = 60.0


def _check_rate_limit(user_id: Any) -> tuple[bool, int]:
    now = time.time()
    with _RATE_LIMIT_LOCK:
        hits = [t for t in _RATE_LIMIT_HITS.get(user_id, []) if now - t < _RATE_LIMIT_WINDOW_SECONDS]
        if len(hits) >= _RATE_LIMIT_MAX_REQUESTS:
            _RATE_LIMIT_HITS[user_id] = hits
            retry_after = max(1, int(_RATE_LIMIT_WINDOW_SECONDS - (now - hits[0])) + 1)
            log.warning("rate limit user=%s (retry=%ds)", user_id, retry_after)
            return False, retry_after
        hits.append(now)
        _RATE_LIMIT_HITS[user_id] = hits
        return True, 0


# --------------------------------------------------------------------------- Utenti

from hemmy.auth.user_context import get_users_store as _users


# --------------------------------------------------------------------------- LLM multi-provider

def _llm_config_candidates(user_id: Any) -> list[dict[str, Any]]:
    from hemmy.core.llm_providers import resolve_llm_config

    try:
        settings = _users().get_settings(user_id) or {}
    except Exception:
        settings = {}

    def _build(provider_name: str, model: str, base_url: str,
               api_key: str, label: str) -> dict[str, Any] | None:
        sub = dict(settings)
        sub["llm_provider"] = provider_name
        if model:
            sub["llm_model"] = model
        if base_url:
            sub["llm_base_url"] = base_url
        try:
            cfg = resolve_llm_config(
                settings=sub,
                api_key=api_key,
                agent_yaml=_cli._load_config(),
            )
        except Exception as exc:
            log_llm.warning("resolve_llm_config fallito per %s: %s", provider_name, exc)
            return None
        return {"provider": provider_name, "config": cfg, "label": label}

    def _get_secret(name: str) -> str | None:
        try:
            return _users().get_secret(user_id, name) or None
        except Exception:
            return None

    candidates: list[dict[str, Any]] = []

    providers_cfg = settings.get("llm_providers")
    if isinstance(providers_cfg, list) and providers_cfg:
        ordered = sorted(
            providers_cfg,
            key=lambda p: (0 if p.get("is_primary") else 1),
        )
        for p in ordered:
            name = str(p.get("provider") or "").strip()
            if not name:
                continue
            slug = _slugify(name)
            api_key = _get_secret("llm_api_key__" + slug)
            if not api_key and p.get("is_primary"):
                api_key = _get_secret("llm_api_key")
            if not api_key:
                continue
            label = "primario" if p.get("is_primary") else "fallback"
            built = _build(
                name,
                str(p.get("model") or ""),
                str(p.get("base_url") or ""),
                api_key,
                label,
            )
            if built:
                candidates.append(built)

    if not candidates:
        api_key = _get_secret("llm_api_key")
        if api_key:
            name = str(settings.get("llm_provider") or "DeepSeek")
            model = str(settings.get("llm_model") or "")
            base_url = str(settings.get("llm_base_url") or "")
            built = _build(name, model, base_url, api_key, "primario")
            if built:
                candidates.append(built)

    return candidates


def _llm_config_for_user(user_id: Any) -> dict[str, Any] | None:
    cands = _llm_config_candidates(user_id)
    if not cands:
        return None
    return cands[0]["config"]


def _bearer_token(request: Any) -> str | None:
    return _jwt_extract_token(request)


def _current_user(request: Any) -> dict[str, Any] | None:
    """Estrae l'utente dal JWT e ne risolve il ruolo.

    Il ruolo viene cercato in ordine:
      1. tabella `profiles` (fonte di verita', via service client);
      2. `app_metadata.role` del JWT (server-controlled, affidabile);
      3. `user_metadata.role` del JWT (user-controlled, ultimo fallback);
      4. default "engineer".

    A differenza della versione precedente, se il lookup su `profiles` fallisce
    l'eccezione NON viene piu' inghiottita: viene loggata. Cosi' se il service
    client non e' configurato lo si vede subito nei log del server invece di
    ritrovarsi con un 403 "accesso riservato" senza spiegazioni.
    """
    tok = _jwt_extract_token(request)
    if not tok:
        return None
    try:
        payload = verify_supabase_jwt(tok)
    except Exception as exc:
        log.warning("JWT rifiutato: %s: %s", type(exc).__name__, exc)
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    email = payload.get("email")
    meta = payload.get("user_metadata") or {}
    app_meta = payload.get("app_metadata") or {}
    username = meta.get("username") or (email.split("@")[0] if email else None)

    role = (
        app_meta.get("role")
        or meta.get("role")
        or "engineer"
    )
    role_source = "jwt"

    try:
        from hemmy.db.supabase_client import get_service_client
        sb = get_service_client()
        prof = (
            sb.table("profiles")
            .select("username, role")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if getattr(prof, "data", None):
            username = prof.data.get("username") or username
            if prof.data.get("role"):
                role = prof.data["role"]
                role_source = "profiles"
    except Exception as exc:
        log.warning(
            "lookup profilo su 'profiles' fallito per user=%s (%s: %s); "
            "uso role=%r dal JWT (source=%s)",
            user_id, type(exc).__name__, exc, role, role_source,
        )

    return {
        "id": user_id,
        "email": email,
        "username": username,
        "role": role,
        "_role_source": role_source,
    }


def _require_admin(u: dict[str, Any] | None, request: Any) -> Any:
    """Ritorna None se l'utente e' admin/founder. Altrimenti ritorna una
    JSONResponse (401/403) gia' pronta, con `role_seen` per diagnosi."""
    from fastapi.responses import JSONResponse as _JR
    if not u:
        return _JR({"ok": False, "error": "non autenticato"}, status_code=401)
    if u.get("role") not in ("admin", "founder"):
        log.warning(
            "accesso admin negato: user=%s role=%r source=%s path=%s",
            u.get("id"), u.get("role"), u.get("_role_source"),
            getattr(request, "url", None),
        )
        return _JR(
            {
                "ok": False,
                "error": "accesso riservato",
                "role_seen": u.get("role"),
                "role_source": u.get("_role_source"),
            },
            status_code=403,
        )
    return None


def _bind_user_agent(session: "_WebSession", user_id: Any) -> None:
    """Costruisce (una sola volta per sessione) l'agente dell'utente."""
    if session.agent is not None:
        return

    candidates = _llm_config_candidates(user_id)
    if not candidates:
        raise ValueError(
            "Nessuna API key LLM configurata. Vai in Impostazioni e "
            "inserisci la tua chiave personale."
        )

    last_exc: Exception | None = None
    for idx, cand in enumerate(candidates):
        try:
            session.agent = _cli._build_agent(
                approval_fn=session.approval_fn,
                secret_prompt_fn=session.secret_fn,
                user_id=user_id,
                llm_config=cand["config"],
            )
        except Exception as exc:
            last_exc = exc
            log_llm.warning("init provider %s fallito: %s", cand["provider"], exc)
            continue

        session.llm_candidates = candidates
        session.llm_active_idx = idx
        if idx > 0:
            log_llm.info("uso fallback %s (primario non disponibile: %s)",
                         cand["provider"], last_exc)
        else:
            log_llm.info("agente costruito con provider %s", cand["provider"])

        try:
            messages = _users().get_conversation(user_id)
            if messages:
                session.agent.load_conversation(messages)
                log_llm.debug("caricati %d messaggi di history", len(messages))
        except Exception:
            pass
        return

    raise ValueError(
        "Nessun provider LLM disponibile. Verifica le tue chiavi in Impostazioni. "
        f"Dettaglio: {last_exc}"
    )


# --------------------------------------------------------------------------- Worker

def _run_turn(session: "_WebSession", uid: Any, question: str) -> None:
    from hemmy.auth.oauth_providers import set_current_user, clear_current_user

    set_current_user(uid)
    try:
        _run_turn_inner(session, uid, question)
    finally:
        clear_current_user()


def _run_turn_inner(session: "_WebSession", uid: Any, question: str) -> None:
    reload_tools = {"flag": False, "ui": ""}
    session.stop_requested = False

    convo_snapshot = list(getattr(session.agent, "conversation", []) or [])

    if uid is not None:
        try:
            _users().set_pending(uid, question)
        except Exception:
            pass

    def _on_event(ev: dict[str, Any]) -> None:
        if session.stop_requested:
            log_turn.info("stop_requested attivo, abortisco turno user=%s", uid)
            raise _TurnCancelled()

        ev_type = ev.get("type")
        if ev_type == "thought":
            log_turn.debug("thought: %s", str(ev.get("text", ""))[:200])
        elif ev_type == "action":
            log_turn.info("tool %s user=%s", ev.get("action"), uid,
                          extra={"user_id": uid, "phase": "tool.call",
                                 "tool": ev.get("action")})
        elif ev_type == "observation":
            text = str(ev.get("text", ""))
            log_turn.info("tool result %s (%d byte) user=%s", ev.get("action"),
                          len(text), uid,
                          extra={"user_id": uid, "phase": "tool.result",
                                 "tool": ev.get("action")})
        elif ev_type == "final":
            log_turn.info("final answer (%d byte) user=%s",
                          len(str(ev.get("text", ""))), uid)
        elif ev_type == "error":
            log_turn.error("evento di errore user=%s: %s", uid, ev.get("text"))

        if ev_type == "observation" and ev.get("action") in (
            "meta.install_tool", "meta.remove_plugin"
        ):
            text = str(ev.get("text", "")).lower().replace(" ", "")
            if '"reload_required":true' in text:
                reload_tools["flag"] = True
            if '"ui_change"' in text and '"ui_change":null' not in text:
                reload_tools["ui"] = "1"
        session.events.put(ev)

    candidates = getattr(session, "llm_candidates", None) or []
    active_idx = getattr(session, "llm_active_idx", 0)
    last_exc: Exception | None = None
    turn_succeeded = False

    if not candidates and uid is not None:
        candidates = _llm_config_candidates(uid)
        active_idx = 0
        session.llm_candidates = candidates
        session.llm_active_idx = 0

    provider_active = (
        session.llm_candidates[session.llm_active_idx]["provider"]
        if session.llm_candidates and 0 <= session.llm_active_idx < len(session.llm_candidates)
        else "none"
    )
    log_turn.info("turno avviato user=%s provider=%s q=%.80r",
                  uid, provider_active, question,
                  extra={"user_id": uid, "phase": "turn.start",
                         "provider": provider_active})
    _t0_turn = time.time()

    for idx in range(active_idx, len(candidates)):
        cand = candidates[idx]

        if idx > active_idx:
            session.events.put({
                "type": "thought",
                "text": (
                    "Provider precedente non disponibile. "
                    "Provo il fallback " + cand["provider"] + "."
                ),
            })
            try:
                session.agent = _cli._build_agent(
                    approval_fn=session.approval_fn,
                    secret_prompt_fn=session.secret_fn,
                    user_id=uid,
                    llm_config=cand["config"],
                )
                try:
                    session.agent.load_conversation(convo_snapshot)
                except Exception:
                    pass
                session.llm_active_idx = idx
            except Exception as exc:
                last_exc = exc
                log_turn.warning("fallback %s init fallito: %s", cand["provider"], exc)
                continue

        agent = session.agent
        if agent is None:
            last_exc = RuntimeError("nessun agente disponibile per il turno")
            continue

        try:
            with timed(log_turn, f"esecuzione agente ({cand['provider']})",
                       user_id=uid, phase="turn.run", provider=cand["provider"]):
                agent.run(question, on_event=_on_event)
            turn_succeeded = True
            last_exc = None
            break
        except _TurnCancelled:
            log_turn.info("turno interrotto su richiesta utente",
                          extra={"user_id": uid, "phase": "turn.stopped"})
            session.events.put({
                "type": "stopped",
                "text": "Elaborazione interrotta su richiesta dell'utente.",
            })
            turn_succeeded = True
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if _is_llm_auth_error(exc):
                log_turn.warning("provider %s errore auth, tento fallback: %s",
                                 cand["provider"], exc,
                                 extra={"user_id": uid, "phase": "turn.auth_error",
                                        "provider": cand["provider"]})
                continue
            log_turn.error("turno fallito: %s: %s", type(exc).__name__, exc,
                           exc_info=True,
                           extra={"user_id": uid, "phase": "turn.error",
                                  "provider": cand["provider"]})
            break

    if not turn_succeeded and last_exc is not None:
        session.events.put({
            "type": "error",
            "text": f"{type(last_exc).__name__}: {last_exc}",
        })

    agent = session.agent
    if uid is not None and agent is not None:
        try:
            _users().save_conversation(uid, agent.conversation)
            _users().set_pending(uid, None)
        except Exception:
            log_turn.warning("salvataggio conversazione fallito user=%s", uid)

    if reload_tools["flag"]:
        try:
            _hot_reload_tools(session)
        except Exception as exc:
            session.events.put({"type": "error", "text": f"reload tool fallito: {exc}"})

    session.busy = False
    session.stop_requested = False
    session.pending = None
    session.pending_payload = None
    session.events.put({"type": "done"})

    dur_ms = int((time.time() - _t0_turn) * 1000)
    log_turn.info("turno concluso user=%s success=%s durata=%dms",
                  uid, turn_succeeded, dur_ms,
                  extra={"user_id": uid, "phase": "turn.end",
                         "duration_ms": dur_ms})

    if reload_tools["flag"]:
        session.events.put({
            "type": "tools_changed",
            "text": "Catalogo tool aggiornato senza riavvio.",
            "ui": bool(reload_tools["ui"]),
        })


# --------------------------------------------------------------------------- App

def create_app() -> Any:
    app = FastAPI(title="Hemmy", docs_url=None, redoc_url=None)

    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import Response

    _default_origins = (
        "http://127.0.0.1:8080,http://localhost:8080,"
        "http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:8765,http://localhost:8765,"
        "https://portal.hemmy.it,https://admin.hemmy.it,"
        "https://admin-hemmy.web.app,https://admin-hemmy.firebaseapp.com"
    )
    _allowed_origins = [
        o.strip()
        for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")
        if o.strip()
    ]

    _allow_origin_regex = (
        r"^https?://("
        r"[a-z0-9-]+\.trycloudflare\.com"
        r"|[a-z0-9-]+\.web\.app"
        r"|[a-z0-9-]+\.firebaseapp\.com"
        # URL di default assegnati da Cloud Run (prima di mappare un dominio
        # custom come portal.hemmy.it): sia il formato legacy
        # "servizio-hash-regione.a.run.app" sia quello attuale
        # "servizio-hash.regione.run.app".
        r"|[a-z0-9-]+\.a\.run\.app"
        r"|[a-z0-9-]+\.[a-z0-9-]+\.run\.app"
        r"|localhost(:\d+)?"
        r"|127\.0\.0\.1(:\d+)?"
        r")$"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_origin_regex=_allow_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
        expose_headers=["*"],
        max_age=3600,
    )

    @app.get("/favicon.ico")
    def favicon() -> Any:
        return Response(status_code=204)

    @app.post("/api/register")
    async def register_gone() -> Any:
        return JSONResponse(
            {"ok": False, "error": "Registrazione gestita da Supabase Auth (client-side)."},
            status_code=410,
        )

    @app.post("/api/login")
    async def login_gone() -> Any:
        return JSONResponse(
            {"ok": False, "error": "Login gestito da Supabase Auth (client-side)."},
            status_code=410,
        )

    @app.post("/api/logout")
    def logout(request: Request) -> Any:
        u = _current_user(request)
        if u:
            _drop_session(u["id"])
            log.info("logout user=%s", u["id"])
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        return {
            "ok": True,
            "user": u,
            "settings": _users().get_settings(u["id"]),
            "secret_names": _users().list_secret_names(u["id"]),
        }

    @app.post("/api/connect/azure")
    def connect_azure(request: Request) -> Any:
        from hemmy.auth.oauth_providers import start_azure_connect
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        try:
            data = start_azure_connect(u["id"])
        except Exception as exc:
            log.warning("connect azure fallito user=%s: %s", u["id"], exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        log.info("connect azure avviato user=%s", u["id"])
        return {"ok": True, **data}

    @app.post("/api/connect/azure/disconnect")
    def disconnect_azure_route(request: Request) -> Any:
        from hemmy.auth.oauth_providers import disconnect_azure, disconnect_azure_devops
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        disconnect_azure(u["id"])
        disconnect_azure_devops(u["id"])
        log.info("disconnect azure user=%s", u["id"])
        return {"ok": True}

    @app.post("/api/connect/azdo")
    async def connect_azdo(request: Request) -> Any:
        from hemmy.auth.oauth_providers import connect_azure_devops
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        try:
            info = connect_azure_devops(u["id"], body.get("organization", ""))
        except RuntimeError as exc:
            log.warning("connect azdo fallito user=%s: %s", u["id"], exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        log.info("connect azdo ok user=%s org=%s", u["id"], body.get("organization"))
        return {"ok": True, **info}

    @app.post("/api/connect/azdo/disconnect")
    def disconnect_azdo_route(request: Request) -> Any:
        from hemmy.auth.oauth_providers import disconnect_azure_devops
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        disconnect_azure_devops(u["id"])
        log.info("disconnect azdo user=%s", u["id"])
        return {"ok": True}

    @app.post("/api/connect/github")
    def connect_github(request: Request) -> Any:
        from hemmy.auth.oauth_providers import start_github_connect
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        try:
            data = start_github_connect(_users(), u["id"])
        except RuntimeError as exc:
            log.warning("connect github fallito user=%s: %s", u["id"], exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        log.info("connect github avviato user=%s", u["id"])
        return {"ok": True, **data}

    @app.post("/api/connect/github/disconnect")
    def disconnect_github_route(request: Request) -> Any:
        from hemmy.auth.oauth_providers import disconnect_github
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        disconnect_github(u["id"])
        log.info("disconnect github user=%s", u["id"])
        return {"ok": True}

    @app.post("/api/connect/github/client-id")
    async def set_github_client_id(request: Request) -> Any:
        from hemmy.auth.oauth_providers import save_github_oauth_client_id
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        try:
            save_github_oauth_client_id(_users(), u["id"], body.get("client_id", ""))
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            log.error("salvataggio github client id fallito user=%s: %s", u["id"], exc)
            return JSONResponse(
                {"ok": False, "error": f"salvataggio fallito: {exc}"},
                status_code=500,
            )
        log.info("github client id salvato user=%s", u["id"])
        return {"ok": True}

    @app.delete("/api/connect/github/client-id")
    def clear_github_client_id(request: Request) -> Any:
        from hemmy.auth.oauth_providers import GITHUB_CLIENT_ID_SECRET
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        try:
            _users().delete_secret(u["id"], GITHUB_CLIENT_ID_SECRET)
        except Exception:
            pass
        log.info("github client id rimosso user=%s", u["id"])
        return {"ok": True}

    @app.post("/api/connect/gcp")
    def connect_gcp(request: Request) -> Any:
        from hemmy.auth.oauth_providers import start_gcp_connect
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        try:
            data = start_gcp_connect(u["id"])
        except RuntimeError as exc:
            log.warning("connect gcp fallito user=%s: %s", u["id"], exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        log.info("connect gcp avviato user=%s", u["id"])
        return {"ok": True, **data}

    @app.post("/api/connect/gcp/disconnect")
    def disconnect_gcp_route(request: Request) -> Any:
        from hemmy.auth.oauth_providers import disconnect_gcp_oauth
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        disconnect_gcp_oauth(u["id"])
        log.info("disconnect gcp user=%s", u["id"])
        return {"ok": True}

    @app.post("/api/connect/aws")
    async def connect_aws(request: Request) -> Any:
        from hemmy.auth.oauth_providers import start_aws_sso_connect
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()

        start_url = (body.get("start_url") or "").strip()
        if start_url:
            try:
                data = start_aws_sso_connect(
                    u["id"], start_url, body.get("region", "eu-west-1")
                )
            except RuntimeError as exc:
                log.warning("connect aws sso fallito user=%s: %s", u["id"], exc)
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            log.info("connect aws sso avviato user=%s", u["id"])
            return {"ok": True, "mode": "sso", **data}

        try:
            save_aws_credentials(
                _users(), u["id"],
                access_key_id=body.get("access_key_id", ""),
                secret_access_key=body.get("secret_access_key", ""),
                session_token=body.get("session_token", ""),
                region=body.get("region", "eu-west-1"),
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            log.error("salvataggio aws static keys fallito user=%s: %s", u["id"], exc)
            return JSONResponse({"ok": False, "error": f"salvataggio fallito: {exc}"}, status_code=500)
        log.info("connect aws static keys ok user=%s", u["id"])
        return {"ok": True, "mode": "static_keys"}

    @app.post("/api/connect/aws/select")
    async def connect_aws_select(request: Request) -> Any:
        from hemmy.auth.oauth_providers import select_aws_role
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        try:
            select_aws_role(u["id"], body.get("account_id", ""), body.get("role_name", ""))
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        log.info("aws role selezionato user=%s account=%s role=%s",
                 u["id"], body.get("account_id"), body.get("role_name"))
        return {"ok": True}

    @app.post("/api/connect/aws/disconnect")
    def disconnect_aws_route(request: Request) -> Any:
        from hemmy.auth.oauth_providers import disconnect_aws_sso
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        disconnect_aws_sso(u["id"])
        disconnect_aws(_users(), u["id"])
        log.info("disconnect aws user=%s", u["id"])
        return {"ok": True}

    @app.post("/api/connect/airflow")
    async def connect_airflow(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        try:
            info = save_airflow_credentials(
                _users(), u["id"],
                base_url=body.get("base_url", ""),
                auth_mode=body.get("auth_mode", "basic"),
                username=body.get("username", ""),
                password=body.get("password", ""),
                token=body.get("token", ""),
                verify_ssl=bool(body.get("verify_ssl", True)),
            )
        except ValueError as exc:
            log.warning("connect airflow fallito user=%s: %s", u["id"], exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            log.error("salvataggio airflow fallito user=%s: %s", u["id"], exc)
            return JSONResponse(
                {"ok": False, "error": f"salvataggio fallito: {exc}"},
                status_code=500,
            )
        log.info("connect airflow ok user=%s base=%s", u["id"], body.get("base_url"))
        return {"ok": True, **info}

    @app.post("/api/connect/airflow/disconnect")
    def disconnect_airflow_route(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        disconnect_airflow(_users(), u["id"])
        log.info("disconnect airflow user=%s", u["id"])
        return {"ok": True}

    @app.get("/api/connect/status")
    def connect_status(request: Request) -> Any:
        from hemmy.auth.oauth_providers import (
            azure_status as _azure_status,
            azure_devops_status as _azdo_status,
            github_status as _github_status,
            gcp_status as _gcp_status,
            aws_sso_status as _aws_sso_status,
            is_github_oauth_configured,
            is_gcp_oauth_configured,
        )
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)

        aws_oauth = _aws_sso_status(u["id"])
        aws_static = _aws_static_status(_users(), u["id"])
        if aws_oauth["status"] != "disconnected":
            aws_view = {**aws_oauth, "mode": "sso"}
        elif aws_static["status"] != "disconnected":
            aws_view = {**aws_static, "connected": True, "mode": "static_keys"}
        else:
            aws_view = {"connected": False, "status": "disconnected"}

        return {
            "ok": True,
            "azure": _azure_status(u["id"]),
            "azdo": _azdo_status(u["id"]),
            "github": _github_status(u["id"]),
            "github_configured": is_github_oauth_configured(_users(), u["id"]),
            "gcp": _gcp_status(u["id"]),
            "gcp_configured": is_gcp_oauth_configured(),
            "aws": aws_view,
            "airflow": _airflow_status(_users(), u["id"]),
        }

    @app.post("/api/settings")
    async def save_settings(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        try:
            merged = _users().set_settings(u["id"], body.get("settings") or {})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        try:
            session = _get_session(u["id"])
            session.agent = None
            session.llm_candidates = []
            session.llm_active_idx = 0
        except Exception:
            pass

        log.info("settings aggiornate user=%s", u["id"])
        return {"ok": True, "settings": merged}

    @app.post("/api/settings/secret")
    async def save_secret(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        name = (body.get("name") or "").strip()
        value = body.get("value") or ""
        if not name or not value:
            return JSONResponse({"ok": False, "error": "name e value richiesti"}, status_code=400)
        try:
            _users().set_secret(u["id"], name, value)
        except Exception as exc:
            log.error("salvataggio segreto %s fallito user=%s: %s", name, u["id"], exc)
            return JSONResponse(
                {"ok": False, "error": f"salvataggio segreto fallito: {exc}"},
                status_code=500,
            )

        try:
            if name == "llm_api_key" or name.startswith("llm_api_key__"):
                session = _get_session(u["id"])
                session.agent = None
                session.llm_candidates = []
                session.llm_active_idx = 0
        except Exception:
            pass

        log.info("segreto salvato user=%s name=%s", u["id"], name)
        return {"ok": True}

    @app.delete("/api/settings/secret")
    async def delete_secret(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name richiesto"}, status_code=400)
        try:
            _users().delete_secret(u["id"], name)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"rimozione fallita: {exc}"},
                status_code=500,
            )

        try:
            if name == "llm_api_key" or name.startswith("llm_api_key__"):
                session = _get_session(u["id"])
                session.agent = None
                session.llm_candidates = []
                session.llm_active_idx = 0
        except Exception:
            pass

        log.info("segreto rimosso user=%s name=%s", u["id"], name)
        return {"ok": True}

    @app.get("/api/llm/providers")
    def llm_providers(request: Request) -> Any:
        if not _current_user(request):
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        from hemmy.core.llm_providers import provider_catalog
        return {"ok": True, "providers": provider_catalog()}

    @app.post("/api/ask")
    async def ask(request: Request) -> Any:
        _u = _current_user(request)
        if not _u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)

        allowed, retry_after = _check_rate_limit(_u["id"])
        if not allowed:
            return JSONResponse(
                {"ok": False, "error": "troppe richieste, riprova tra poco"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        session = _get_session(_u["id"])
        try:
            _bind_user_agent(session, _u["id"])
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "code": "llm_not_configured", "error": str(exc)},
                status_code=400,
            )
        except Exception as exc:
            log.error("bind agente fallito user=%s: %s", _u["id"], exc)
            return JSONResponse(
                {"ok": False, "code": "agent_init_failed",
                 "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )

        body = await request.json()
        question = (body.get("question") or "").strip()
        if not question:
            return JSONResponse({"ok": False, "error": "domanda vuota"}, status_code=400)
        if session.busy:
            log.warning("/api/ask rifiutato: turno in corso user=%s", _u["id"])
            return JSONResponse(
                {"ok": False, "error": "un turno è già in corso"}, status_code=409
            )
        session.busy = True
        session.stop_requested = False
        session.pending = None
        session.pending_payload = None
        session.events.put({"type": "user", "text": question})
        log.info("POST /api/ask user=%s q=%.80r", _u["id"], question)
        threading.Thread(
            target=_run_turn, args=(session, _u["id"], question), daemon=True
        ).start()
        return {"ok": True}

    @app.post("/api/stop")
    def stop(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        session = _get_session(u["id"])
        if not session.busy:
            log.info("POST /api/stop: nessun turno in corso user=%s", u["id"])
            return {"ok": True, "stopped": False, "message": "nessun turno in corso"}
        log.warning("POST /api/stop: stop richiesto user=%s", u["id"])
        session.stop_requested = True
        session.events.put({"type": "stopping"})
        return {"ok": True, "stopped": True}

    @app.post("/api/respond")
    async def respond(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        body = await request.json()
        session = _get_session(u["id"])
        session.answer.put(body.get("value"))
        return {"ok": True}

    @app.get("/api/events")
    def events(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        session = _get_session(u["id"])

        def stream() -> Any:
            log_session.info("SSE aperto user=%s", u["id"])
            try:
                from hemmy.plugins import load_user_plugins
                _plugins = [p["name"] for p in load_user_plugins(u["id"]) if "error" not in p]
            except Exception:
                _plugins = []
            hello = {
                "type": "hello",
                "busy": session.busy,
                "plugins": _plugins,
                "llm_provider": (
                    session.llm_candidates[session.llm_active_idx]["provider"]
                    if session.llm_candidates and 0 <= session.llm_active_idx < len(session.llm_candidates)
                    else None
                ),
            }
            yield f"data: {json.dumps(hello, ensure_ascii=False)}\n\n"
            try:
                while True:
                    try:
                        ev = session.events.get(timeout=8)
                        log_session.debug("SSE yield user=%s type=%s",
                                          u["id"], ev.get("type"))
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                log_session.info("SSE chiuso user=%s", u["id"])

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/history")
    def history(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        session = _get_session(u["id"])
        try:
            _bind_user_agent(session, u["id"])
        except ValueError:
            return {
                "messages": [], "busy": False, "pending": None,
                "pending_kind": None, "pending_payload": None,
            }
        agent = session.agent
        messages = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in agent.conversation
            if m.get("role") != "system"
        ]
        try:
            pending = _users().get_pending(u["id"])
        except Exception:
            pending = None
        return {
            "messages": messages,
            "busy": session.busy,
            "pending": pending,
            "pending_kind": session.pending,
            "pending_payload": session.pending_payload,
        }

    @app.get("/api/tools")
    def tools_catalog(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        session = _get_session(u["id"])
        try:
            _bind_user_agent(session, u["id"])
        except ValueError as exc:
            return {"total": 0, "plugins": [], "tools": [], "error": str(exc)}
        except Exception as exc:
            return {"total": 0, "plugins": [], "tools": [],
                    "error": f"{type(exc).__name__}: {exc}"}
        agent = session.agent
        if agent is None:
            return {"total": 0, "plugins": [], "tools": []}
        try:
            from hemmy.plugins import load_user_plugins
            plugin_names = {p["name"] for p in load_user_plugins(u["id"]) if "error" not in p}
        except Exception:
            plugin_names = set()
        items = []
        for name in sorted(agent.tool_docs):
            try:
                write = agent.guardrails.is_write_action(name)
            except Exception:
                write = False
            doc = str(agent.tool_docs.get(name, "")).replace("[WRITE]", "").strip()
            items.append({
                "name": name,
                "domain": name.split(".", 1)[0],
                "doc": doc,
                "write": write,
                "plugin": name in plugin_names,
            })
        return {"total": len(items), "plugins": sorted(plugin_names), "tools": items}

    @app.get("/api/dashboard")
    def dashboard(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        session = _get_session(u["id"])
        try:
            _bind_user_agent(session, u["id"])
        except ValueError as exc:
            return {"busy": False, "messages": [], "infrastructure": {},
                    "mermaid": "", "audit": [], "error": str(exc)}
        except Exception as exc:
            return {"busy": False, "messages": [], "infrastructure": {},
                    "mermaid": "", "audit": [],
                    "error": f"{type(exc).__name__}: {exc}"}
        agent = session.agent
        if agent is None:
            return {"busy": False, "messages": [], "infrastructure": {}, "mermaid": "", "audit": []}
        messages = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in agent.conversation
            if m.get("role") != "system"
        ]
        infra: dict[str, Any] = {}
        mermaid = ""
        try:
            infra = agent.docs.state
            md = agent.docs.render_markdown()
            mermaid = _extract_mermaid(md)
        except Exception:
            pass
        audit = _read_audit_for_user(u["id"], limit=200)
        return {
            "busy": session.busy,
            "messages": messages,
            "infrastructure": infra,
            "mermaid": mermaid,
            "audit": audit,
        }

    @app.post("/api/upload")
    async def upload(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        import base64
        body = await request.json()
        name = os.path.basename(str(body.get("name", ""))).strip()
        b64 = body.get("content_b64", "")
        if not name or not b64:
            return JSONResponse(
                {"ok": False, "error": "name e content_b64 richiesti"}, status_code=400
            )
        try:
            data = base64.b64decode(b64.split(",")[-1])
        except Exception:
            return JSONResponse({"ok": False, "error": "base64 non valido"}, status_code=400)
        uploads_dir = _cli.user_uploads_dir(u["id"])
        os.makedirs(uploads_dir, exist_ok=True)
        path = os.path.join(uploads_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        log.info("upload user=%s name=%s size=%d", u["id"], name, len(data))
        return {"ok": True, "name": name, "path": path, "size": len(data)}

    @app.get("/api/health")
    def health() -> Any:
        return {"ok": True}

    @app.post("/api/reset")
    def reset(request: Request) -> Any:
        u = _current_user(request)
        if not u:
            return JSONResponse({"ok": False, "error": "non autenticato"}, status_code=401)
        session = _get_session(u["id"])
        try:
            session.agent.reset_conversation()
        except Exception:
            pass
        try:
            _users().reset_conversation(u["id"])
        except Exception:
            pass
        session.busy = False
        session.stop_requested = False
        session.pending = None
        session.pending_payload = None
        session.events.put({"type": "reset"})
        log.info("reset conversazione user=%s", u["id"])
        return {"ok": True}

    # --------------------------------------------------------------------- Admin

    @app.get("/api/admin/native-tools")
    def admin_native_tools(request: Request) -> Any:
        u = _current_user(request)
        denied = _require_admin(u, request)
        if denied is not None:
            return denied

        from hemmy.interfaces.cli import TOOL_DOCS as _DOCS

        seen: set[str] = set()
        tools: list[dict[str, Any]] = []

        for name in sorted(_DOCS.keys()):
            raw = str(_DOCS.get(name, ""))
            tools.append({
                "name": name,
                "domain": name.split(".", 1)[0] if "." in name else name,
                "doc": raw.replace("[WRITE]", "").strip(),
                "write": "[WRITE]" in raw,
                "origin": "core",
            })
            seen.add(name)

        try:
            from hemmy.plugins import load_native_plugins
            for p in load_native_plugins():
                if "error" in p or not p.get("name"):
                    continue
                nm = p["name"]
                if nm in seen:
                    continue
                seen.add(nm)
                raw_doc = str(p.get("doc", ""))
                tools.append({
                    "name": nm,
                    "domain": nm.split(".", 1)[0] if "." in nm else nm,
                    "doc": raw_doc.replace("[WRITE]", "").strip(),
                    "write": bool(p.get("write", False)),
                    "origin": "native_plugins",
                })
        except Exception:
            pass

        return {"ok": True, "total": len(tools), "tools": tools}

    @app.post("/api/admin/promote-plugin")
    async def admin_promote_plugin(request: Request) -> Any:
        u = _current_user(request)
        denied = _require_admin(u, request)
        if denied is not None:
            return denied

        body = await request.json()
        plugin_id = (body.get("plugin_id") or "").strip()
        if not plugin_id:
            return JSONResponse({"ok": False, "error": "plugin_id richiesto"}, status_code=400)

        from hemmy.db.supabase_client import is_supabase_configured, get_admin_client
        if not is_supabase_configured():
            return JSONResponse({"ok": False, "error": "Supabase non configurato"}, status_code=500)

        sb = get_admin_client()

        try:
            row = sb.table("plugins").select("*").eq("id", plugin_id).single().execute()
            plugin = row.data
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"plugin non trovato: {exc}"}, status_code=404)
        if not plugin:
            return JSONResponse({"ok": False, "error": "plugin non trovato"}, status_code=404)
        if plugin.get("status") == "promoted_native":
            return JSONResponse({"ok": False, "error": "plugin già promosso"}, status_code=400)

        source = plugin.get("source_code") or ""
        module_name = plugin.get("module_name") or ""
        manifest = plugin.get("manifest") or {}
        tools_field = manifest.get("tools") if isinstance(manifest, dict) else None
        if not isinstance(tools_field, list) or not tools_field:
            return JSONResponse({"ok": False, "error": "manifest privo di tools"}, status_code=400)

        first = tools_field[0] or {}
        tool_name = first.get("name") or ""
        if not tool_name or not module_name:
            return JSONResponse(
                {"ok": False, "error": "nome tool o modulo mancanti nel manifest"},
                status_code=400,
            )

        from hemmy.tools import meta as meta_tools
        try:
            v = meta_tools._validate_code(tool_name, source)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"validazione fallita: {exc}"},
                status_code=500,
            )
        if v.get("errors"):
            return JSONResponse(
                {"ok": False, "error": "validazione fallita", "errors": v["errors"]},
                status_code=400,
            )

        import os as _os2
        try:
            try:
                from hemmy.plugins import native_plugins_dir as _npd
                dest_dir = str(_npd())
            except Exception:
                dest_dir = str(_cli.PROJECT_ROOT / "native_plugins")
            _os2.makedirs(dest_dir, exist_ok=True)
            dest_path = _os2.path.join(dest_dir, module_name)
            tmp_path = dest_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(source)
            _os2.replace(tmp_path, dest_path)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"scrittura in native_plugins fallita: {exc}"},
                status_code=500,
            )

        db_warning = None
        try:
            sb.table("plugins").update({
                "status": "promoted_native",
            }).eq("id", plugin_id).execute()
        except Exception as exc:
            db_warning = f"file copiato ma aggiornamento DB fallito: {exc}"

        log.info("plugin promosso a nativo: %s (%s) da user=%s",
                 tool_name, module_name, u["id"])

        result: dict[str, Any] = {
            "ok": True,
            "promoted": tool_name,
            "module_name": module_name,
            "dest": dest_path,
            "message": (
                f"Plugin '{tool_name}' promosso a tool nativo. "
                "Sarà disponibile per tutti gli utenti al prossimo riavvio "
                "dell'agente (i nativi si leggono all'avvio)."
            ),
        }
        if db_warning:
            result["db_warning"] = db_warning
        return result

    @app.post("/api/admin/review-plugin")
    async def admin_review_plugin(request: Request) -> Any:
        """Approva / rifiuta / rimette in review un plugin utente.

        Corpo JSON: { "plugin_id": "...", "action": "approve" | "reject" | "reset" }

        Transizioni: approve -> approved, reject -> rejected, reset -> pending.
        """
        u = _current_user(request)
        denied = _require_admin(u, request)
        if denied is not None:
            return denied

        body = await request.json()
        plugin_id = (body.get("plugin_id") or "").strip()
        action = (body.get("action") or "").strip().lower()

        if not plugin_id:
            return JSONResponse({"ok": False, "error": "plugin_id richiesto"}, status_code=400)
        if action not in ("approve", "reject", "reset"):
            return JSONResponse(
                {"ok": False, "error": "action deve essere approve | reject | reset"},
                status_code=400,
            )

        from hemmy.db.supabase_client import is_supabase_configured, get_admin_client
        if not is_supabase_configured():
            return JSONResponse({"ok": False, "error": "Supabase non configurato"}, status_code=500)

        sb = get_admin_client()

        try:
            row = sb.table("plugins").select("*").eq("id", plugin_id).single().execute()
            plugin = row.data
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"plugin non trovato: {exc}"},
                status_code=404,
            )
        if not plugin:
            return JSONResponse({"ok": False, "error": "plugin non trovato"}, status_code=404)

        current_status = (plugin.get("status") or "").strip()
        if current_status == "promoted_native":
            return JSONResponse(
                {"ok": False, "error": "plugin già promosso a nativo, stato non modificabile"},
                status_code=400,
            )

        if action == "approve":
            source = plugin.get("source_code") or ""
            manifest = plugin.get("manifest") or {}
            tools_field = manifest.get("tools") if isinstance(manifest, dict) else None
            tool_name = ""
            if isinstance(tools_field, list) and tools_field:
                tool_name = str((tools_field[0] or {}).get("name") or "")
            if tool_name and source:
                from hemmy.tools import meta as meta_tools
                try:
                    v = meta_tools._validate_code(tool_name, source)
                except Exception as exc:
                    return JSONResponse(
                        {"ok": False, "error": f"validazione fallita: {exc}"},
                        status_code=500,
                    )
                if v.get("errors"):
                    return JSONResponse(
                        {"ok": False, "error": "validazione fallita", "errors": v["errors"]},
                        status_code=400,
                    )

        new_status = {
            "approve": "approved",
            "reject": "rejected",
            "reset": "pending",
        }[action]

        try:
            sb.table("plugins").update({
                "status": new_status,
            }).eq("id", plugin_id).execute()
        except Exception as exc:
            log.error("review plugin fallita user=%s plugin=%s action=%s: %s",
                      u["id"], plugin_id, action, exc)
            return JSONResponse(
                {"ok": False, "error": f"aggiornamento fallito: {exc}"},
                status_code=500,
            )

        log.info("plugin review user=%s plugin=%s action=%s new_status=%s",
                 u["id"], plugin_id, action, new_status)
        return {"ok": True, "status": new_status, "action": action}

    # --------------------------------------------------------------------- Frontend statico
    import os as _os
    from fastapi.staticfiles import StaticFiles

    _fe_dir = _os.path.join(str(_cli.PROJECT_ROOT), "frontend")
    if _os.path.isdir(_fe_dir):
        app.mount("/", StaticFiles(directory=_fe_dir, html=True), name="frontend")
        log.info("frontend montato da %s", _fe_dir)
    else:
        log.warning("cartella frontend non trovata: %s", _fe_dir)

    return app


def _extract_mermaid(markdown: str) -> str:
    start = markdown.find("```mermaid")
    if start == -1:
        return ""
    start = markdown.find("\n", start) + 1
    end = markdown.find("```", start)
    return markdown[start:end].strip() if end != -1 else ""


def _read_audit(path: str, limit: int = 200) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return list(reversed(rows))


def _read_audit_for_user(user_id: Any, limit: int = 200) -> list[dict[str, Any]]:
    from hemmy.db.supabase_client import is_supabase_configured

    if not is_supabase_configured():
        return _read_audit(_cli.AUDIT_PATH, limit=limit)

    from hemmy.db.supabase_client import get_admin_client

    try:
        resp = (
            get_admin_client()
            .table("audit_log")
            .select("action,args,outcome,error,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        return []
    rows = resp.data or []
    return [
        {
            "timestamp": r.get("created_at"),
            "action": r.get("action"),
            "args": r.get("args"),
            "outcome": r.get("outcome"),
            "error": r.get("error"),
        }
        for r in rows
    ]


def serve(host: str | None = None, port: int | None = None) -> None:
    """Avvia il server uvicorn.

    Default HOST `0.0.0.0` (non `127.0.0.1`): necessario in QUALUNQUE contesto
    a container (Cloud Run, Docker in generale) perché il load balancer/health
    check si connette all'IP del container, non a loopback — Cloud Run imposta
    solo `PORT` nell'ambiente, mai `HOST`. Stesso default già usato da
    `frontend/serve.py`. Per un bind solo-locale esplicito: `HOST=127.0.0.1`.
    """
    if host is None:
        host = os.environ.get("HOST", "0.0.0.0")
    if port is None:
        try:
            port = int(os.environ.get("PORT", "8765"))
        except (TypeError, ValueError):
            port = 8765

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("uvicorn non installato. Esegui: pip install fastapi uvicorn") from exc

    setup_logging()
    log.info("avvio Hemmy backend su http://%s:%d", host, port)
    app = create_app()
    print(f"Hemmy backend -> http://{host}:{port}  (Ctrl+C per uscire)")
    uvicorn.run(app, host=host, port=port, log_level="warning")