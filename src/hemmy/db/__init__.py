"""Persistenza condivisa (Supabase/Postgres) per utenti, chat, tool e audit-log.

Se `SUPABASE_URL` non è impostata, il resto dell'app ripiega sullo storage locale
(SQLite + file), utile per sviluppo offline e per i test (nessuna rete richiesta).
"""

from __future__ import annotations

from hemmy.db.supabase_client import is_supabase_configured

__all__ = ["is_supabase_configured"]
