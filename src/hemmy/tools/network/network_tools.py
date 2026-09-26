"""Tool di rete (Azure Network) — lettura/diagnosi.

Il provisioning della rete (VNet, subnet, Private Endpoint, DNS privati) si fa via
Terraform (IaC-first). Questi tool servono a ispezionare e documentare la rete esistente.
Tutti in sola lettura.
"""

from __future__ import annotations

from typing import Any


def _rg_from_id(resource_id: str | None) -> str | None:
    if not resource_id:
        return None
    parts = resource_id.split("/")
    return parts[4] if len(parts) > 4 else None


def list_virtual_networks(
    client: Any, resource_group: str | None = None
) -> list[dict[str, Any]]:
    """Elenca le VNet (per resource group o intera subscription)."""
    items = (
        client.virtual_networks.list(resource_group)
        if resource_group
        else client.virtual_networks.list_all()
    )
    result = []
    for v in items:
        space = v.address_space.address_prefixes if v.address_space else []
        result.append(
            {
                "name": v.name,
                "address_space": list(space or []),
                "location": v.location,
                "resource_group": _rg_from_id(v.id),
            }
        )
    return result


def list_subnets(client: Any, resource_group: str, vnet_name: str) -> list[dict[str, Any]]:
    """Elenca le subnet di una VNet."""
    return [
        {"name": s.name, "address_prefix": s.address_prefix}
        for s in client.subnets.list(resource_group, vnet_name)
    ]


def list_private_endpoints(
    client: Any, resource_group: str | None = None
) -> list[dict[str, Any]]:
    """Elenca i Private Endpoint (per resource group o subscription)."""
    items = (
        client.private_endpoints.list(resource_group)
        if resource_group
        else client.private_endpoints.list_by_subscription()
    )
    result = []
    for pe in items:
        conns = []
        for c in (pe.private_link_service_connections or []):
            conns.append(
                {
                    "name": c.name,
                    "private_link_resource_id": c.private_link_service_id,
                    "group_ids": list(c.group_ids or []),
                }
            )
        result.append(
            {
                "name": pe.name,
                "subnet": pe.subnet.id if pe.subnet else None,
                "connections": conns,
            }
        )
    return result


def list_network_security_groups(
    client: Any, resource_group: str | None = None
) -> list[dict[str, Any]]:
    """Elenca gli NSG (per resource group o subscription)."""
    items = (
        client.network_security_groups.list(resource_group)
        if resource_group
        else client.network_security_groups.list_all()
    )
    return [{"name": n.name, "location": n.location} for n in items]
