"""UserStore backed by Supabase (Postgres + Auth) — stessa interfaccia pubblica di
`auth.users.UserStore` (SQLite), così `interfaces/web.py` non deve sapere quale
backend è attivo: la scelta è un dettaglio di `_users()` in base a `SUPABASE_URL`.

Autenticazione: delegata a Supabase Auth (email + password).
- `register()` usa l'Admin API (`auth.admin.create_user`, service_role key) con
  `email_confirm=True`: niente email di conferma da configurare, l'utente esiste
  subito. Lo username/ruolo vanno nella riga `profiles` collegata (trigger DB +
  update esplicito qui per il ruolo).
- `authenticate()` usa il client anon (`sign_in_with_password`): è l'unica chiamata
  che deve rispettare RLS/le regole di Supabase Auth, non bypassarle.

Sessioni: Supabase restituisce una coppia (access_token, refresh_token) JWT al
login. Per restare compatibili con l'interfaccia esistente — `authenticate()` poi
`create_session(user_id)` restituisce UN token da mettere in cookie — i due JWT
vengono incollati in un unico token opaco "access::refresh"; `resolve_session()`
li separa e verifica l'access token localmente (firma HS256 col JWT secret del
progetto), senza bisogno di una chiamata di rete ad ogni richiesta.
"""

from __future__ import annotations

import time
from typing import Any

from hemmy.auth import secrets_crypto

_VALID_ROLES = {"architect", "engineer", "analyst"}


class SupabaseUserStore:
    def __init__(
        self,
        admin_client: Any = None,
        anon_client: Any = None,
        jwt_secret_value: str | None = None,
        master_key_path: str | None = None,
    ) -> None:
        """I client sono iniettabili per i test (nessuna rete richiesta).

        Se omessi, vengono costruiti pigramente da `db.supabase_client` al primo
        utilizzo reale (così l'import del modulo non richiede env var presenti).
        """
        self._admin_client = admin_client
        self._anon_client = anon_client
        self._jwt_secret_value = jwt_secret_value
        self._master_key_path = master_key_path
        # Tokens prodotti da un login riuscito, in attesa che create_session() li
        # consumi: vita brevissima, solo in memoria di processo.
        self._pending_tokens: dict[Any, tuple[str, str]] = {}

    # --------------------------------------------------------------- clients
    def _admin(self) -> Any:
        if self._admin_client is None:
            from hemmy.db.supabase_client import get_admin_client

            self._admin_client = get_admin_client()
        return self._admin_client

    def _anon(self) -> Any:
        if self._anon_client is None:
            from hemmy.db.supabase_client import get_anon_client

            self._anon_client = get_anon_client()
        return self._anon_client

    def _jwt_secret(self) -> str:
        if self._jwt_secret_value is None:
            from hemmy.db.supabase_client import jwt_secret

            self._jwt_secret_value = jwt_secret()
        return self._jwt_secret_value

    def _master_key(self) -> bytes:
        return secrets_crypto.load_master_key(self._master_key_path)

    # ------------------------------------------------------------------ users
    def register(
        self, username: str, password: str, role: str = "engineer", email: str | None = None
    ) -> dict[str, Any]:
        username = (username or "").strip().lower()
        email = (email or "").strip().lower()
        if not username or len(username) < 3:
            raise ValueError("username troppo corto (min 3 caratteri)")
        if not email or "@" not in email:
            raise ValueError("email non valida (richiesta da Supabase Auth)")
        if not password or len(password) < 8:
            raise ValueError("password troppo corta (min 8 caratteri)")
        if role not in _VALID_ROLES:
            raise ValueError(f"ruolo non valido: {role} (attesi {sorted(_VALID_ROLES)})")

        try:
            resp = self._admin().auth.admin.create_user(
                {
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"username": username},
                }
            )
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"registrazione fallita: {exc}") from exc
        user_id = resp.user.id
        # Il trigger DB crea già la riga in profiles con username dai metadata;
        # qui fissiamo il ruolo (default 'engineer' altrimenti).
        self._admin().table("profiles").update({"role": role}).eq("id", user_id).execute()
        return {"id": user_id, "username": username, "role": role}

    def authenticate(self, identifier: str, password: str) -> dict[str, Any] | None:
        """`identifier` = email (Supabase Auth richiede email, non username)."""
        email = (identifier or "").strip().lower()
        try:
            result = self._anon().auth.sign_in_with_password({"email": email, "password": password})
        except Exception:  # noqa: BLE001 - credenziali errate o utente assente
            return None
        session = getattr(result, "session", None)
        user = getattr(result, "user", None)
        if session is None or user is None:
            return None
        self._pending_tokens[user.id] = (session.access_token, session.refresh_token)
        profile = self.get_user(user.id) or {"id": user.id, "username": email, "role": "engineer"}
        return profile

    def count_users(self) -> int:
        resp = self._admin().table("profiles").select("id", count="exact").execute()
        return int(resp.count or 0)

    def get_user(self, user_id: Any) -> dict[str, Any] | None:
        resp = self._admin().table("profiles").select("id,username,role").eq("id", user_id).limit(1).execute()
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    # --------------------------------------------------------------- sessions
    def create_session(self, user_id: Any, ttl: int = 0) -> str:
        """Restituisce il token opaco "access::refresh" prodotto dall'ultimo
        authenticate() riuscito per questo utente. `ttl` è ignorato: la scadenza
        è quella dell'access token JWT (impostazione del progetto Supabase)."""
        tokens = self._pending_tokens.pop(user_id, None)
        if tokens is None:
            raise ValueError("nessuna sessione Supabase in sospeso per questo utente: rifai il login")
        access, refresh = tokens
        return f"{access}::{refresh}"

    def resolve_session(self, token: str) -> dict[str, Any] | None:
        if not token or "::" not in token:
            return None
        access, _refresh = token.split("::", 1)
        import jwt as pyjwt

        try:
            claims = pyjwt.decode(
                access, self._jwt_secret(), algorithms=["HS256"], audience="authenticated"
            )
        except Exception:  # noqa: BLE001 - scaduto, firma non valida, malformato
            return None
        user_id = claims.get("sub")
        if not user_id:
            return None
        return self.get_user(user_id)

    def revoke_session(self, token: str) -> None:
        if not token or "::" not in token:
            return
        access, _refresh = token.split("::", 1)
        try:
            self._anon().auth.sign_out(access)
        except Exception:  # noqa: BLE001 - best-effort, il cookie viene comunque cancellato
            pass

    # --------------------------------------------------------------- settings
    def get_settings(self, user_id: Any) -> dict[str, Any]:
        resp = self._admin().table("profiles").select("settings").eq("id", user_id).limit(1).execute()
        rows = resp.data or []
        return dict(rows[0]["settings"]) if rows and rows[0].get("settings") else {}

    def set_settings(self, user_id: Any, settings: dict[str, Any]) -> dict[str, Any]:
        forbidden = [
            k for k in settings
            if any(t in k.lower() for t in ("secret", "password", "pwd", "connection_string", "token", "api_key"))
        ]
        if forbidden:
            raise ValueError(f"chiavi segrete non ammesse in settings: {forbidden} (usa set_secret)")
        merged = self.get_settings(user_id)
        merged.update(settings)
        self._admin().table("profiles").update({"settings": merged}).eq("id", user_id).execute()
        return merged

    # ----------------------------------------------------------------- secrets
    def set_secret(self, user_id: Any, name: str, value: str) -> None:
        enc = secrets_crypto.encrypt(value, self._master_key())
        self._admin().table("user_secrets").upsert(
            {"user_id": user_id, "name": name, "value_enc": enc}
        ).execute()

    def get_secret(self, user_id: Any, name: str) -> str | None:
        resp = (
            self._admin()
            .table("user_secrets")
            .select("value_enc")
            .eq("user_id", user_id)
            .eq("name", name)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        return secrets_crypto.decrypt(rows[0]["value_enc"], self._master_key())

    def list_secret_names(self, user_id: Any) -> list[str]:
        resp = self._admin().table("user_secrets").select("name").eq("user_id", user_id).execute()
        return sorted(r["name"] for r in (resp.data or []))

    # ------------------------------------------------------- conversazioni per-utente
    def get_conversation(self, user_id: Any) -> list[dict[str, Any]]:
        resp = (
            self._admin()
            .table("conversations")
            .select("messages")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return []
        data = rows[0].get("messages")
        return data if isinstance(data, list) else []

    def save_conversation(self, user_id: Any, messages: list[dict[str, Any]]) -> None:
        self._admin().table("conversations").upsert(
            {"user_id": user_id, "messages": messages, "updated_at": _iso_now()}
        ).execute()

    def reset_conversation(self, user_id: Any) -> None:
        self._admin().table("conversations").upsert(
            {"user_id": user_id, "messages": [], "pending": None, "updated_at": _iso_now()}
        ).execute()

    def set_pending(self, user_id: Any, text: str | None) -> None:
        self._admin().table("conversations").upsert(
            {"user_id": user_id, "pending": text, "updated_at": _iso_now()}
        ).execute()

    def get_pending(self, user_id: Any) -> str | None:
        resp = (
            self._admin()
            .table("conversations")
            .select("pending")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0].get("pending") if rows else None

    def close(self) -> None:
        pass


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
