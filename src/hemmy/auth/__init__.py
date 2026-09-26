"""Autenticazione e gestione utenti della piattaforma multi-utente.

Due backend, stessa interfaccia pubblica (scelto da `interfaces.web._users()` in
base a `SUPABASE_URL`):
- `UserStore` (SQLite): storage locale, zero setup, usato di default/nei test.
- `SupabaseUserStore` (Postgres + Supabase Auth): storage condiviso per il
  deployment multi-istanza (es. Cloud Run).

Principio di sicurezza (coerente col resto del progetto): NON si memorizzano
credenziali cloud (Azure/GitHub) — quelle restano OAuth per-utente in memoria
(`auth.oauth_providers`), mai a riposo. Qui si salva solo: account applicativo,
config non-segreta, e l'eventuale API key LLM cifrata con una master key.
"""

from hemmy.auth.supabase_users import SupabaseUserStore
from hemmy.auth.users import UserStore

__all__ = ["UserStore", "SupabaseUserStore"]
