"""Tool per gli Azure Management Lock (governance).

Livelli: 'CanNotDelete' (blocca l'eliminazione) e 'ReadOnly' (blocca modifiche ed
eliminazione). Proteggono le risorse di produzione da azioni accidentali — anche
dell'agente stesso.

NB: NON gestiscono i lock transitori del control-plane (es.
'StorageAccountOperationInProgress'), che non hanno API e vanno solo attesi.
"""

from __future__ import annotations

from typing import Any

_LEVELS = {"CanNotDelete", "ReadOnly"}


def list_locks(client: Any, resource_group: str | None = None) -> list[dict[str, Any]]:
    """Elenca i Management Lock (per resource group o intera subscription)."""
    if resource_group:
        items = client.management_locks.list_at_resource_group_level(resource_group)
    else:
        items = client.management_locks.list_at_subscription_level()
    return [
        {"name": lk.name, "level": lk.level, "notes": lk.notes, "id": lk.id}
        for lk in items
    ]


def create_lock(
    client: Any,
    resource_group: str,
    lock_name: str,
    level: str = "CanNotDelete",
    notes: str | None = None,
) -> dict[str, Any]:
    """[WRITE] Crea un Management Lock a livello di resource group."""
    if level not in _LEVELS:
        raise ValueError(f"Livello non valido: '{level}'. Usa uno di {sorted(_LEVELS)}.")

    from azure.mgmt.resource.locks.models import ManagementLockObject

    lock = client.management_locks.create_or_update_at_resource_group_level(
        resource_group, lock_name, ManagementLockObject(level=level, notes=notes)
    )
    return {"created_lock": lock.name, "level": lock.level, "resource_group": resource_group}


def delete_lock(client: Any, resource_group: str, lock_name: str) -> dict[str, Any]:
    """[WRITE] Rimuove un Management Lock a livello di resource group."""
    client.management_locks.delete_at_resource_group_level(resource_group, lock_name)
    return {"deleted_lock": lock_name, "resource_group": resource_group}
