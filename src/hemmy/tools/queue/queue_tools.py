"""Tool per Azure Queue Storage.

Lettura (list) ed azioni di scrittura (create/delete). Le scritture passano dal
guardrail (approvazione umana). Usano il QueueServiceClient di `infra/clients.py`.
"""

from __future__ import annotations

from typing import Any


def list_queues(client: Any) -> list[str]:
    """Elenca le code (Queue Storage) dell'account configurato."""
    return [q.name for q in client.list_queues()]


# ===================================================================== WRITE


def create_queue(client: Any, queue_name: str) -> dict[str, Any]:
    """Crea una coda (Queue Storage)."""
    client.create_queue(queue_name)
    return {"created_queue": queue_name}


def delete_queue(client: Any, queue_name: str) -> dict[str, Any]:
    """Elimina una coda (Queue Storage) e i suoi messaggi."""
    client.delete_queue(queue_name)
    return {"deleted_queue": queue_name}
