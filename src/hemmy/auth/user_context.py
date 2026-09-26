"""Contesto "utente corrente" per i tool nativi (inclusi quelli generati a
runtime e promossi) che devono leggere un SEGRETO PER-UTENTE — non un token
OAuth (quello ha già `oauth_providers.get_current_azure_credential()` /
`get_current_github_token()`), ma credenziali applicative come Airflow,
Databricks, dbt, SMTP, Telegram, ecc. salvate cifrate in `user_secrets`.

Due pezzi, entrambi già esistenti altrove e qui solo CENTRALIZZATI perché
`interfaces/web.py._users()` non è importabile da un tool nativo senza
tirarsi dietro FastAPI:

1. `get_current_user_id()` — chi sta eseguendo il tool nel THREAD CORRENTE.
   Stesso thread-local di `oauth_providers` (`_run_turn` in web.py chiama
   `set_current_user(user_id)` prima di eseguire qualunque tool): un tool
   può quindi sapere per conto di chi sta girando SENZA che l'argomento
   `user_id`/`owner_user_id` gli arrivi dal modello (che resta vietato,
   vedi `core/agent.py._RESERVED_ARG_NAMES`).
2. `get_users_store()` — lo stesso UserStore (Supabase o SQLite) usato dal
   layer web, per leggere/scrivere segreti cifrati per-utente. Lazy e
   condiviso a livello di processo (comportamento identico a `web.py._users()`,
   di cui questa è ora la fonte di verità: `web.py` la riusa).
"""

from __future__ import annotations

import os
from typing import Any


def get_current_user_id() -> Any:
    """Utente "legato" al thread corrente (impostato da `_run_turn` prima di
    eseguire i tool del turno). None se non c'è un utente (CLI locale)."""
    from hemmy.auth.oauth_providers import _local

    return getattr(_local, "user_id", None)


_STORE: Any = None


def get_users_store() -> Any:
    """UserStore condiviso (lazy): Supabase se configurata, altrimenti SQLite
    locale — stessa logica di `interfaces/web.py._users()`."""
    global _STORE
    if _STORE is None:
        from hemmy.db.supabase_client import is_supabase_configured

        if is_supabase_configured():
            from hemmy.auth.supabase_users import SupabaseUserStore

            _STORE = SupabaseUserStore()
        else:
            from hemmy.auth.users import UserStore
            from hemmy.interfaces.cli import PROJECT_ROOT

            db = os.path.join(str(PROJECT_ROOT), "data", "users.db")
            _STORE = UserStore(db)
    return _STORE


def get_current_user_secret(name: str) -> str | None:
    """Segreto `name` dell'utente corrente (thread-local), o None se non
    c'è un utente legato o il segreto non è impostato. Non solleva mai."""
    uid = get_current_user_id()
    if uid is None:
        return None
    try:
        return get_users_store().get_secret(uid, name) or None
    except Exception:  # noqa: BLE001
        return None


def get_current_user_secret_or_env(name: str, env_name: str | None = None) -> str | None:
    """Come `get_current_user_secret`, ma con un fallback a variabile d'ambiente
    SOLO quando non c'è alcun utente legato al thread corrente (CLI locale a
    singolo operatore, senza portale — `user_id is None`). In un processo web
    multi-utente questo fallback non scatta mai: un tool non deve MAI leggere
    una credenziale condivisa da env var per conto di un utente autenticato,
    userebbe la stessa credenziale per tutti. `env_name` di default è `name`
    in maiuscolo (convenzione usata dai tool nativi, es. 'databricks_token' ->
    'DATABRICKS_TOKEN')."""
    if get_current_user_id() is not None:
        return get_current_user_secret(name)
    return os.environ.get(env_name or name.upper())
