"""Persistenza cifrata delle sessioni OAuth (Azure, GitHub).

I token sono cifrati lato backend prima dell'INSERT. Il DB vede solo ciphertext.
Chiavi logiche:
  - Azure:  {"msal_cache": "<serialized>", "home_account_id": "upn@tenant"}
  - GitHub: {"access_token": "gho_...", "login": "user", "id": 12345}
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------- Save / Load

def save_oauth_session(users: Any, user_id: Any, provider: str, payload: dict,
                       expires_at: datetime | None = None) -> None:
    """Serializza il payload in JSON, lo cifra e lo salva."""
    provider = (provider or "").strip().lower()
    if provider not in ("azure", "github"):
        raise ValueError(f"provider non supportato: {provider}")
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    # Usa lo stesso storage cifrato di user_secrets (set_secret cifra già).
    users.set_secret(user_id, f"oauth_{provider}_session", raw)

    # Salva anche la scadenza in settings (non segreta).
    try:
        settings = users.get_settings(user_id) or {}
        oauth_meta = settings.get("oauth_meta") or {}
        oauth_meta[provider] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        users.set_settings(user_id, {"oauth_meta": oauth_meta})
    except Exception:  # noqa: BLE001
        pass


def load_oauth_session(users: Any, user_id: Any, provider: str) -> dict | None:
    """Rilegge e decifra il payload. Ritorna None se assente o corrotto."""
    provider = (provider or "").strip().lower()
    try:
        raw = users.get_secret(user_id, f"oauth_{provider}_session")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[oauth_persistence] JSON corrotto per {provider}: {exc}", file=sys.stderr)
        return None


def delete_oauth_session(users: Any, user_id: Any, provider: str) -> None:
    """Cancella la sessione OAuth salvata (al disconnect)."""
    provider = (provider or "").strip().lower()
    try:
        users.delete_secret(user_id, f"oauth_{provider}_session")
    except Exception:  # noqa: BLE001
        pass