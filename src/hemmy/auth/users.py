"""User store multi-utente su SQLite (solo stdlib + pynacl).

Cosa gestisce:
- **Account**: registrazione e login con password hashed (pbkdf2_hmac-sha256, salt per-utente).
- **Sessioni**: token opachi con scadenza, per gating della UI web.
- **Settings per-utente**: config NON-segreta (subscription, tenant, RG, factory, repo, modello LLM).
- **Segreti per-utente**: es. API key LLM, cifrata a riposo con `nacl.secret.SecretBox`
  usando una master key locale (`config/.master.key`, gitignored). Il valore in chiaro
  esiste solo in memoria al momento dell'uso.

Cosa NON gestisce (per scelta di sicurezza): credenziali cloud Azure/GitHub — restano
login personale interattivo (`az login` / `gh auth login`), nessun segreto cloud a riposo.

Ruoli: 'architect' | 'engineer' | 'analyst' (informativi; l'autorizzazione reale sulle
risorse resta quella dell'identità personale su Azure — i fallimenti RBAC sono corretti).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from hemmy.auth import secrets_crypto

_PBKDF2_ITERS = 200_000
_SESSION_TTL = 12 * 3600  # 12 ore
_VALID_ROLES = {"architect", "engineer", "analyst"}


def _now() -> int:
    return int(time.time())


class UserStore:
    """Store SQLite per utenti, sessioni, settings e segreti cifrati."""

    def __init__(self, db_path: str, master_key_path: str | None = None) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._master_key_path = master_key_path or str(Path(self.db_path).parent / ".master.key")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ----------------------------------------------------------------- schema
    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                pwd_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'engineer',
                created_at INTEGER NOT NULL,
                settings TEXT NOT NULL DEFAULT '{}',
                secrets_enc TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS conversations (
                user_id INTEGER PRIMARY KEY,
                messages TEXT NOT NULL DEFAULT '[]',
                pending TEXT,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        self._conn.commit()
        # Migrazione morbida: DB creati prima dell'introduzione dell'email.
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(users)")}
        if "email" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            self._conn.commit()

    # ------------------------------------------------------------- master key
    def _master_key(self) -> bytes:
        # SECRETS_MASTER_KEY (env) ha priorità; altrimenti file locale (dev).
        return secrets_crypto.load_master_key(self._master_key_path)

    def _encrypt(self, plaintext: str) -> str:
        return secrets_crypto.encrypt(plaintext, self._master_key())

    def _decrypt(self, token: str) -> str:
        return secrets_crypto.decrypt(token, self._master_key())

    # ----------------------------------------------------------------- passwords
    @staticmethod
    def _hash_pwd(password: str, salt: bytes) -> str:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
        return base64.b64encode(dk).decode()

    # ------------------------------------------------------------------- users
    def register(
        self, username: str, password: str, role: str = "engineer", email: str | None = None
    ) -> dict[str, Any]:
        username = (username or "").strip().lower()
        email = (email or "").strip().lower() or None
        if not username or len(username) < 3:
            raise ValueError("username troppo corto (min 3 caratteri)")
        if not password or len(password) < 8:
            raise ValueError("password troppo corta (min 8 caratteri)")
        if role not in _VALID_ROLES:
            raise ValueError(f"ruolo non valido: {role} (attesi {sorted(_VALID_ROLES)})")
        salt = secrets.token_bytes(16)
        try:
            cur = self._conn.execute(
                "INSERT INTO users (username, email, pwd_hash, salt, role, created_at) VALUES (?,?,?,?,?,?)",
                (username, email, self._hash_pwd(password, salt), base64.b64encode(salt).decode(), role, _now()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"username o email già registrati: {username}") from exc
        return {"id": cur.lastrowid, "username": username, "role": role}

    def authenticate(self, identifier: str, password: str) -> dict[str, Any] | None:
        """`identifier` = username oppure email (case-insensitive), indifferentemente."""
        identifier = (identifier or "").strip().lower()
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?", (identifier, identifier)
        ).fetchone()
        if row is None:
            return None
        salt = base64.b64decode(row["salt"])
        if not secrets.compare_digest(self._hash_pwd(password, salt), row["pwd_hash"]):
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

    def count_users(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    # --------------------------------------------------------------- sessions
    def create_session(self, user_id: int, ttl: int = _SESSION_TTL) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self._conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + ttl),
        )
        self._conn.commit()
        return token

    def resolve_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        row = self._conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] < _now():
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self._conn.commit()
            return None
        return self.get_user(row["user_id"])

    def revoke_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self._conn.commit()

    # ---------------------------------------------------------------- settings
    def get_settings(self, user_id: int) -> dict[str, Any]:
        row = self._conn.execute("SELECT settings FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["settings"])
        except (TypeError, json.JSONDecodeError):
            return {}

    def set_settings(self, user_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        """Salva config NON-segreta. Rifiuta chiavi che sembrano segreti."""
        forbidden = [k for k in settings if any(t in k.lower() for t in ("secret", "password", "pwd", "connection_string", "token", "api_key"))]
        if forbidden:
            raise ValueError(f"chiavi segrete non ammesse in settings: {forbidden} (usa set_secret)")
        merged = self.get_settings(user_id)
        merged.update(settings)
        self._conn.execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(merged), user_id))
        self._conn.commit()
        return merged

    # ----------------------------------------------------------------- secrets
    def _get_secrets_enc(self, user_id: int) -> dict[str, str]:
        row = self._conn.execute("SELECT secrets_enc FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["secrets_enc"])
        except (TypeError, json.JSONDecodeError):
            return {}

    def set_secret(self, user_id: int, name: str, value: str) -> None:
        """Cifra e memorizza un segreto per-utente (es. 'deepseek_api_key')."""
        store = self._get_secrets_enc(user_id)
        store[name] = self._encrypt(value)
        self._conn.execute("UPDATE users SET secrets_enc = ? WHERE id = ?", (json.dumps(store), user_id))
        self._conn.commit()

    def get_secret(self, user_id: int, name: str) -> str | None:
        """Restituisce il segreto in chiaro (solo in memoria). None se assente."""
        store = self._get_secrets_enc(user_id)
        enc = store.get(name)
        return self._decrypt(enc) if enc else None

    def list_secret_names(self, user_id: int) -> list[str]:
        """Solo i NOMI dei segreti memorizzati (mai i valori)."""
        return sorted(self._get_secrets_enc(user_id).keys())

    # ------------------------------------------------------- conversazioni per-utente
    def get_conversation(self, user_id: int) -> list[dict[str, Any]]:
        """Cronologia isolata dell'utente (lista messaggi). Vuota se assente.

        Isolamento privacy: ogni utente vede SOLO la propria conversazione.
        """
        row = self._conn.execute(
            "SELECT messages FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return []
        try:
            data = json.loads(row["messages"])
            return data if isinstance(data, list) else []
        except (TypeError, json.JSONDecodeError):
            return []

    def save_conversation(self, user_id: int, messages: list[dict[str, Any]]) -> None:
        """Salva/aggiorna la cronologia dell'utente (upsert)."""
        self._conn.execute(
            "INSERT INTO conversations (user_id, messages, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET messages=excluded.messages, "
            "updated_at=excluded.updated_at",
            (user_id, json.dumps(messages, ensure_ascii=False), _now()),
        )
        self._conn.commit()

    def reset_conversation(self, user_id: int) -> None:
        """Azzera la cronologia (e il pending) dell'utente."""
        self._conn.execute(
            "INSERT INTO conversations (user_id, messages, pending, updated_at) VALUES (?, '[]', NULL, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET messages='[]', pending=NULL, updated_at=excluded.updated_at",
            (user_id, _now()),
        )
        self._conn.commit()

    def set_pending(self, user_id: int, text: str | None) -> None:
        """Registra (o azzera) l'intento in corso: usato per riprendere dopo un riavvio.

        Prima di eseguire un turno si salva la domanda; a turno concluso si azzera.
        Se resta valorizzato, significa che un turno è stato interrotto (crash/deploy).
        """
        self._conn.execute(
            "INSERT INTO conversations (user_id, pending, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET pending=excluded.pending, "
            "updated_at=excluded.updated_at",
            (user_id, text, _now()),
        )
        self._conn.commit()

    def get_pending(self, user_id: int) -> str | None:
        """Intento non completato dell'utente (None se nessun turno è rimasto in sospeso)."""
        row = self._conn.execute(
            "SELECT pending FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["pending"] if row and row["pending"] else None

    def close(self) -> None:
        self._conn.close()
