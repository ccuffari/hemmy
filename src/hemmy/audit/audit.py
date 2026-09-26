"""Audit-log delle azioni di scrittura.

Registra su file (una riga JSON per evento) chi/cosa/quando/esito di ogni azione
[WRITE], con redaction dei segreti. Utile per tracciabilità e compliance: l'agente
può modificare risorse Azure, quindi ogni scrittura va tracciata.
"""

from __future__ import annotations

import getpass
import json
import os
from datetime import datetime, timezone
from typing import Any

from hemmy.utils.helpers import redact_secrets


class AuditLog:
    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            self.user = getpass.getuser()
        except Exception:  # noqa: BLE001 - fallback robusto
            self.user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

    def record(
        self,
        action: str,
        args: dict[str, Any],
        outcome: str,
        error: Any = None,
    ) -> None:
        """Scrive un evento di audit (append). `outcome`: success|error|denied."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": self.user,
            "action": action,
            "args": redact_secrets(json.dumps(args, default=str, ensure_ascii=False)),
            "outcome": outcome,
        }
        if error is not None:
            entry["error"] = redact_secrets(str(error))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
