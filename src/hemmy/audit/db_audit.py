"""Audit-log su Supabase — stessa interfaccia pubblica di `audit.audit.AuditLog`
(`record(action, args, outcome, error=None)`), ma la riga finisce in Postgres
invece che in un file JSONL locale.

Perché: su Cloud Run le istanze sono multiple ed effimere — un file locale per
istanza rende il log incompleto e non sopravvive ai restart. Una tabella condivisa
risolve entrambi i problemi, ed è interrogabile (es. "le mie azioni" nella UI).

Il redaction dei segreti resta IDENTICO a prima (stessa funzione `redact_secrets`,
applicata prima dell'INSERT): a riposo nel DB non deve mai finire un segreto.
"""

from __future__ import annotations

import getpass
import json
import os
from datetime import datetime, timezone
from typing import Any

from hemmy.utils.helpers import redact_secrets


class SupabaseAuditLog:
    def __init__(self, user_id: Any = None, admin_client: Any = None) -> None:
        """`user_id` = utente applicativo (profiles.id) collegato all'azione, se noto
        (nel contesto web è sempre noto; in CLI locale resta None)."""
        self._user_id = user_id
        self._admin_client = admin_client
        try:
            self.user = getpass.getuser()
        except Exception:  # noqa: BLE001
            self.user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

    def _admin(self) -> Any:
        if self._admin_client is None:
            from hemmy.db.supabase_client import get_admin_client

            self._admin_client = get_admin_client()
        return self._admin_client

    def record(self, action: str, args: dict[str, Any], outcome: str, error: Any = None) -> None:
        entry: dict[str, Any] = {
            "user_id": self._user_id,
            "action": action,
            "args": json.loads(redact_secrets(json.dumps(args, default=str, ensure_ascii=False))),
            "outcome": outcome,
        }
        if error is not None:
            entry["error"] = redact_secrets(str(error))
        try:
            self._admin().table("audit_log").insert(entry).execute()
        except Exception:  # noqa: BLE001 - un log fallito non deve mai bloccare l'operazione
            pass


def build_audit_log(local_path: str, user_id: Any = None) -> Any:
    """Sceglie il backend: Supabase se configurata, altrimenti file JSONL locale
    (comportamento invariato per sviluppo/CLI offline)."""
    from hemmy.db.supabase_client import is_supabase_configured

    if is_supabase_configured():
        return SupabaseAuditLog(user_id=user_id)
    from hemmy.audit.audit import AuditLog

    return AuditLog(local_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
