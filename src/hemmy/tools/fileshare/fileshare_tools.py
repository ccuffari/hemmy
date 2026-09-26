"""Tool per Azure File Share.

Lettura (list) ed azioni di scrittura (create share/directory, delete share).
Le scritture passano dal guardrail (approvazione umana). Usano il ShareServiceClient
di `infra/clients.py`.
"""

from __future__ import annotations

from typing import Any


def list_shares(client: Any) -> list[str]:
    """Elenca le file share dell'account configurato."""
    return [s.name for s in client.list_shares()]


def list_directories_and_files(
    client: Any, share_name: str, directory_path: str = ""
) -> list[dict[str, Any]]:
    """Elenca directory e file dentro una share (a un dato path)."""
    share_client = client.get_share_client(share_name)
    dir_client = share_client.get_directory_client(directory_path)
    items = []
    for item in dir_client.list_directories_and_files():
        items.append({"name": item["name"], "is_directory": item["is_directory"]})
    return items


# ===================================================================== WRITE


def create_share(client: Any, share_name: str) -> dict[str, Any]:
    """Crea una file share."""
    client.create_share(share_name)
    return {"created_share": share_name}


def create_directory(client: Any, share_name: str, directory_path: str) -> dict[str, Any]:
    """Crea una directory dentro una file share."""
    share_client = client.get_share_client(share_name)
    share_client.create_directory(directory_path)
    return {"created_directory": f"{share_name}/{directory_path}"}


def delete_share(client: Any, share_name: str) -> dict[str, Any]:
    """Elimina una file share e il suo contenuto."""
    client.delete_share(share_name)
    return {"deleted_share": share_name}
