"""Tool RBAC — gestione dei role assignment (Azure Authorization).

- list_role_assignments: lettura (diagnosi di chi ha cosa a un dato scope).
- assign_role / remove_role_assignment: scrittura (approvazione umana).

NB: l'assegnazione di ruoli richiede che il principal chiamante abbia
'Microsoft.Authorization/roleAssignments/write' (Owner o User Access Administrator).
Senza, Azure risponde 403 — non è un problema del tool.
"""

from __future__ import annotations

import uuid
from typing import Any


def _role_definition_id(client: Any, scope: str, role_name: str) -> str:
    roles = list(
        client.role_definitions.list(scope, filter=f"roleName eq '{role_name}'")
    )
    if not roles:
        raise ValueError(f"Ruolo '{role_name}' non trovato allo scope {scope}.")
    return roles[0].id


def list_role_assignments(client: Any, scope: str) -> list[dict[str, Any]]:
    """Elenca i role assignment a un dato scope (RG, risorsa, subscription)."""
    result = []
    for a in client.role_assignments.list_for_scope(scope):
        result.append(
            {
                "principal_id": a.principal_id,
                "role_definition_id": a.role_definition_id,
                "scope": a.scope,
            }
        )
    return result


def assign_role(
    client: Any,
    scope: str,
    principal_id: str,
    role_name: str,
    principal_type: str = "ServicePrincipal",
) -> dict[str, Any]:
    """[WRITE] Assegna un ruolo a un principal allo scope indicato.

    scope: es. /subscriptions/<sub>/resourceGroups/<rg> oppure l'id di una risorsa.
    principal_type: ServicePrincipal | User | Group.
    """
    from azure.mgmt.authorization.models import RoleAssignmentCreateParameters

    role_def_id = _role_definition_id(client, scope, role_name)
    params = RoleAssignmentCreateParameters(
        role_definition_id=role_def_id,
        principal_id=principal_id,
        principal_type=principal_type,
    )
    assignment = client.role_assignments.create(scope, str(uuid.uuid4()), params)
    return {
        "assigned_role": role_name,
        "principal_id": principal_id,
        "scope": scope,
        "assignment_id": assignment.name,
    }


def remove_role_assignment(
    client: Any, scope: str, principal_id: str, role_name: str
) -> dict[str, Any]:
    """[WRITE] Rimuove i role assignment di un principal per un ruolo a uno scope."""
    role_def_id = _role_definition_id(client, scope, role_name)
    removed = []
    for a in client.role_assignments.list_for_scope(scope):
        if a.principal_id == principal_id and a.role_definition_id == role_def_id:
            client.role_assignments.delete_by_id(a.id)
            removed.append(a.name)
    return {"removed": removed, "role": role_name, "principal_id": principal_id}
