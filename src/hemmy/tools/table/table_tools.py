"""Tool per Azure Table Storage.

Lettura (list) ed azioni di scrittura (create/delete). Le scritture passano dal
guardrail (approvazione umana). Usano il TableServiceClient di `infra/clients.py`.
"""

from __future__ import annotations

from typing import Any


def list_tables(client: Any) -> list[str]:
    """Elenca le tabelle (Table Storage) dell'account configurato."""
    return [t.name for t in client.list_tables()]


# ===================================================================== WRITE


def create_table(client: Any, table_name: str) -> dict[str, Any]:
    """Crea una tabella (Table Storage)."""
    client.create_table(table_name)
    return {"created_table": table_name}


def delete_table(client: Any, table_name: str) -> dict[str, Any]:
    """Elimina una tabella (Table Storage) e il suo contenuto."""
    client.delete_table(table_name)
    return {"deleted_table": table_name}
