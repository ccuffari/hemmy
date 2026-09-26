"""Naming convention delle risorse Azure (deterministica e configurabile).

Genera nomi conformi alla convention definita in `config/naming.yaml`, rispettando i
vincoli per tipo (es. storage account: minuscolo, alfanumerico, max 24, senza trattini).
"""

from __future__ import annotations

import re
from typing import Any


def build(
    naming_config: dict[str, Any],
    resource_type: str,
    workload: str,
    environment: str,
    region: str = "westeurope",
    instance: str = "01",
) -> dict[str, Any]:
    """Costruisce un nome risorsa conforme alla naming convention.

    resource_type: es. resource_group | storage_account | key_vault | data_factory |
    sql_server | sql_database | virtual_network | subnet | private_endpoint.
    """
    abbrs = naming_config.get("abbreviations", {})
    regions = naming_config.get("regions", {})
    envs = naming_config.get("environments", {})
    no_hyphen = set(naming_config.get("no_hyphen_types", []))
    max_len = naming_config.get("max_len", {})

    abbr = abbrs.get(resource_type)
    if not abbr:
        raise ValueError(
            f"Tipo '{resource_type}' non presente nella naming convention "
            f"(config/naming.yaml). Disponibili: {sorted(abbrs)}."
        )

    env = envs.get(environment, environment)
    reg = regions.get(region, region)
    wl = re.sub(r"[^a-z0-9]", "", workload.lower())

    if resource_type in no_hyphen:
        name = f"{abbr}{wl}{env}{reg}{instance}".lower()
        name = re.sub(r"[^a-z0-9]", "", name)[: max_len.get(resource_type, 24)]
    else:
        name = f"{abbr}-{wl}-{env}-{reg}-{instance}".lower()
        if resource_type in max_len:
            name = name[: max_len[resource_type]]

    return {
        "resource_type": resource_type,
        "name": name,
        "environment": env,
        "region": reg,
        "convention": naming_config.get("pattern"),
    }
