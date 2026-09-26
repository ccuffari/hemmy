"""Client Supabase: due istanze, due scopi distinti.

- `get_admin_client()` — usa `SUPABASE_SERVICE_ROLE_KEY`: bypassa la Row Level
  Security, usato SOLO dal backend per leggere/scrivere qualunque riga (profiles,
  conversations, user_secrets, plugins, audit_log) e per operazioni di
  amministrazione utenti (`auth.admin.create_user`). Questa chiave non deve MAI
  arrivare al frontend.
- `get_anon_client()` — usa `SUPABASE_ANON_KEY`: è la chiave pubblica, l'unica che
  può fare `sign_in_with_password` (login) rispettando RLS. Usata dal backend per
  conto dell'utente durante il login, mai per bypassare permessi.

Se `SUPABASE_URL` manca, l'app è configurata per usare lo storage locale (SQLite +
file) invece di Supabase: `is_supabase_configured()` lo segnala al resto del codice.
"""

from __future__ import annotations

import os
from typing import Any

_ADMIN_CLIENT: Any = None
_ANON_CLIENT: Any = None


def is_supabase_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL"))


def get_admin_client() -> Any:
    """Client con service_role key: bypassa RLS. Solo lato backend."""
    global _ADMIN_CLIENT
    if _ADMIN_CLIENT is None:
        from supabase import create_client

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _ADMIN_CLIENT = create_client(url, key)
    return _ADMIN_CLIENT


def get_anon_client() -> Any:
    """Client con anon key: rispetta RLS, usato per il login (sign-in) utente."""
    global _ANON_CLIENT
    if _ANON_CLIENT is None:
        from supabase import create_client

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_ANON_KEY"]
        _ANON_CLIENT = create_client(url, key)
    return _ANON_CLIENT


def jwt_secret() -> str:
    """Secret HS256 del progetto Supabase: verifica gli access token localmente,
    senza una chiamata di rete ad ogni richiesta."""
    return os.environ["SUPABASE_JWT_SECRET"]
