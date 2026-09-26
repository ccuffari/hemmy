"""Tool per i Resource Group (management-plane, azure-mgmt-resource).

Il resource group è il contenitore di primo livello: qui iniziano le cascate di
creazione (storage account, SQL Server, ecc.). La creazione richiede approvazione
umana (guardrail).
"""

from __future__ import annotations

from typing import Any


def list_resource_groups(client: Any) -> list[dict[str, Any]]:
    """Elenca i resource group della subscription."""
    return [
        {"name": g.name, "location": g.location}
        for g in client.resource_groups.list()
    ]


# ===================================================================== WRITE


def create_resource_group(client: Any, name: str, location: str) -> dict[str, Any]:
    """[WRITE] Crea (o aggiorna) un resource group."""
    group = client.resource_groups.create_or_update(name, {"location": location})
    return {"created_resource_group": group.name, "location": group.location}


def list_resources(client: Any, resource_group: str) -> list[dict[str, Any]]:
    """Elenca le risorse di un resource group (nome, tipo, id)."""
    return [
        {"name": r.name, "type": r.type, "id": r.id}
        for r in client.resources.list_by_resource_group(resource_group)
    ]


def move_resources(
    client: Any,
    source_resource_group: str,
    resource_ids: list[str],
    target_resource_group: str,
) -> dict[str, Any]:
    """[WRITE] Sposta risorse tra resource group (Azure Resource Move).

    Operazione delicata: alcune risorse (es. Private Endpoint, DNS zone, Managed VNet
    di ADF) possono rompersi e vanno ricreate. `target_resource_group` può essere il
    nome o l'id completo.
    """
    import os

    from azure.mgmt.resource.resources.models import ResourcesMoveInfo

    if target_resource_group.startswith("/subscriptions/"):
        target_id = target_resource_group
    else:
        sub = os.environ["ADF_SUBSCRIPTION_ID"]
        target_id = f"/subscriptions/{sub}/resourceGroups/{target_resource_group}"

    poller = client.resources.begin_move_resources(
        source_resource_group,
        ResourcesMoveInfo(resources=resource_ids, target_resource_group=target_id),
    )
    poller.result()
    return {
        "moved": len(resource_ids),
        "source": source_resource_group,
        "target": target_resource_group,
    }
