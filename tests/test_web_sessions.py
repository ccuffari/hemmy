"""Test di isolamento delle sessioni web multi-utente (`interfaces/web.py`).

Verificano il fix di sicurezza critico: prima esisteva un'unica `SESSION`
globale condivisa da TUTTE le richieste del processo. Due utenti concorrenti
potevano vedersi lo streaming SSE a vicenda, dirottarsi il turno in corso, o
risolversi a vicenda le richieste di approvazione (`/api/respond`, che in più
non richiedeva nemmeno autenticazione). Ora ogni utente ha una `_WebSession`
dedicata, indicizzata per `user_id`.

Niente JWT/Supabase reali qui: `_current_user` è monkeypatchato con una mappa
token->utente fittizia, e `_cli._build_agent` è sostituito con un agente finto
(niente Azure/LLM reali) — l'obiettivo è isolare e testare SOLO la logica di
instradamento delle sessioni in `web.py`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest


class _SyncASGIClient:
    """Client HTTP sincrono minimale sopra il trasporto ASGI di httpx.

    `fastapi.testclient.TestClient` (basato su `starlette.testclient`) richiede
    una versione di `httpx` più vecchia di quella installata in questo progetto
    (incompatibilità tra le due dipendenze, non legata al codice applicativo:
    `httpx>=0.28` non accetta più `app=` in `Client.__init__`, e il suo
    `ASGITransport` è ormai solo asincrono). Niente `pytest-asyncio` necessaria:
    ogni chiamata apre un `AsyncClient` e lo esegue con `asyncio.run`.
    """

    def __init__(self, app: Any, base_url: str = "http://testserver") -> None:
        self._app = app
        self._base_url = base_url

    def _run(self, method: str, url: str, **kwargs: Any) -> Any:
        import httpx

        async def _call() -> Any:
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url=self._base_url) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(_call())

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._run("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._run("POST", url, **kwargs)


class _FakeAgent:
    def __init__(self) -> None:
        self.conversation: list[dict[str, Any]] = []

    def load_conversation(self, msgs: list[dict[str, Any]]) -> None:
        self.conversation = list(msgs)

    def reset_conversation(self) -> None:
        self.conversation = []

    def run(self, question: str, on_event: Any = None) -> None:
        self.conversation.append({"role": "user", "content": question})
        self.conversation.append({"role": "assistant", "content": "ok"})
        if on_event:
            on_event({"type": "final_answer", "text": "ok"})


class _FakeUsers:
    def get_conversation(self, uid: Any) -> list[Any]:
        return []

    def save_conversation(self, uid: Any, conv: Any) -> None:
        pass

    def set_pending(self, uid: Any, q: Any) -> None:
        pass

    def get_pending(self, uid: Any) -> Any:
        return None

    def reset_conversation(self, uid: Any) -> None:
        pass


_USERS_BY_TOKEN = {
    "tokA": {"id": "userA", "email": "a@example.com", "username": "a", "role": "engineer"},
    "tokB": {"id": "userB", "email": "b@example.com", "username": "b", "role": "engineer"},
}


def _fake_current_user(request: Any) -> dict[str, Any] | None:
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else None
    return _USERS_BY_TOKEN.get(tok)


@pytest.fixture()
def web_client(monkeypatch):
    from hemmy.interfaces import web as webmod

    # Registro sessioni pulito per ogni test (i test non devono influenzarsi a vicenda).
    monkeypatch.setattr(webmod, "_SESSIONS", {})
    monkeypatch.setattr(webmod, "_RATE_LIMIT_HITS", {})

    monkeypatch.setattr(webmod, "_current_user", _fake_current_user)
    monkeypatch.setattr(
        webmod, "_llm_config_candidates",
        lambda uid: [{"provider": "fake", "config": {}, "label": "primario"}],
    )
    monkeypatch.setattr(
        webmod._cli, "_build_agent",
        lambda approval_fn=None, secret_prompt_fn=None, user_id=None, llm_config=None: _FakeAgent(),
    )
    monkeypatch.setattr(webmod, "_users", lambda: _FakeUsers())

    app = webmod.create_app()
    return _SyncASGIClient(app)


def _wait_until_idle(webmod: Any, user_id: str, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        sess = webmod._get_session(user_id)
        if not sess.busy:
            return
        time.sleep(0.02)
    raise AssertionError(f"turno di {user_id} non completato entro {timeout}s")


def test_two_users_get_distinct_sessions(web_client):
    from hemmy.interfaces import web as webmod

    r = web_client.post(
        "/api/ask", headers={"Authorization": "Bearer tokA"}, json={"question": "ciao"}
    )
    assert r.status_code == 200
    _wait_until_idle(webmod, "userA")

    sess_a = webmod._get_session("userA")
    sess_b = webmod._get_session("userB")

    assert sess_a is not sess_b
    assert sess_a.events is not sess_b.events
    assert sess_a.answer is not sess_b.answer
    # Il turno di A non deve aver toccato l'agente/stato di B.
    assert sess_b.agent is None
    assert sess_a.agent is not None
    assert sess_a.agent.conversation  # A ha ricevuto la sua risposta


def test_respond_requires_auth(web_client):
    r = web_client.post("/api/respond", json={"value": True})
    assert r.status_code == 401


def test_respond_routes_to_the_correct_users_session_only(web_client):
    from hemmy.interfaces import web as webmod

    r = web_client.post(
        "/api/respond", headers={"Authorization": "Bearer tokA"}, json={"value": True}
    )
    assert r.status_code == 200

    sess_a = webmod._get_session("userA")
    sess_b = webmod._get_session("userB")

    assert sess_a.answer.get_nowait() is True
    assert sess_b.answer.empty()  # l'approvazione di A non deve finire a B


def test_logout_drops_only_that_users_session(web_client):
    from hemmy.interfaces import web as webmod

    webmod._get_session("userA")
    webmod._get_session("userB")
    assert "userA" in webmod._SESSIONS
    assert "userB" in webmod._SESSIONS

    r = web_client.post("/api/logout", headers={"Authorization": "Bearer tokA"})
    assert r.status_code == 200
    assert "userA" not in webmod._SESSIONS
    assert "userB" in webmod._SESSIONS


def test_rate_limit_blocks_excessive_requests(web_client, monkeypatch):
    from hemmy.interfaces import web as webmod

    monkeypatch.setattr(webmod, "_RATE_LIMIT_MAX_REQUESTS", 2)

    ok1 = web_client.post(
        "/api/ask", headers={"Authorization": "Bearer tokA"}, json={"question": "1"}
    )
    _wait_until_idle(webmod, "userA")
    ok2 = web_client.post(
        "/api/ask", headers={"Authorization": "Bearer tokA"}, json={"question": "2"}
    )
    _wait_until_idle(webmod, "userA")
    blocked = web_client.post(
        "/api/ask", headers={"Authorization": "Bearer tokA"}, json={"question": "3"}
    )
    assert ok1.status_code == 200
    assert ok2.status_code == 200
    assert blocked.status_code == 429

    # Un utente diverso non deve essere influenzato dal limite di un altro.
    other = web_client.post(
        "/api/ask", headers={"Authorization": "Bearer tokB"}, json={"question": "ciao"}
    )
    assert other.status_code == 200
