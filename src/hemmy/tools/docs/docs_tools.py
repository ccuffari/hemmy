"""Tool di documentazione: sincronizza lo stato reale da Azure e rigenera i doc.

`sync` legge l'infrastruttura corrente (ADF, storage di default, SQL di default e
risorse management-plane nel resource group) e ricostruisce lo stato del DocStore,
quindi salva `INFRASTRUCTURE.md` + diagramma Mermaid. Operazione di sola lettura su
Azure (scrive solo il file di documentazione locale).
"""

from __future__ import annotations

from typing import Any

from hemmy.tools.adf import adf_tools
from hemmy.tools.blob import blob_tools
from hemmy.tools.fileshare import fileshare_tools
from hemmy.tools.network import network_tools
from hemmy.tools.queue import queue_tools
from hemmy.tools.resource import resource_tools
from hemmy.tools.sql import sql_tools
from hemmy.tools.table import table_tools


def add_note(docstore: Any, title: str, content: str) -> dict[str, Any]:
    """Aggiunge/aggiorna una nota custom nel documento e lo rigenera.

    Le note persistono nello stato: sopravvivono a `docs.sync` (upsert per titolo).
    """
    docstore.add_note(title, content)
    docstore.save()
    return {"note": title, "markdown": docstore.markdown_path}


def remove_note(docstore: Any, title: str) -> dict[str, Any]:
    """Rimuove una nota custom e rigenera il documento."""
    existed = docstore.remove_note(title)
    docstore.save()
    return {"removed": title, "existed": existed}


def sync(
    docstore: Any,
    adf_client: Any,
    resource_group_default: str,
    factory_name: str,
    blob_client: Any,
    table_service: Any,
    queue_service: Any,
    share_service: Any,
    sql_conn: Any,
    storage_mgmt_client: Any,
    sql_mgmt_client: Any,
    resource_mgmt_client: Any,
    network_client: Any,
    resource_group: str | None = None,
) -> dict[str, Any]:
    """Ricostruisce la documentazione leggendo lo stato reale da Azure."""
    rg = resource_group or resource_group_default
    summary: dict[str, Any] = {"resource_group": rg, "collected": {}, "errors": {}}

    docstore.reset_state()

    def _try(section: str, fn) -> None:
        try:
            summary["collected"][section] = fn()
        except Exception as exc:  # noqa: BLE001 - una sezione non blocca le altre
            summary["errors"][section] = str(exc)

    # --- Management-plane ---
    def _resource_groups() -> int:
        n = 0
        for g in resource_tools.list_resource_groups(resource_mgmt_client):
            docstore.apply(
                "resource.create_resource_group",
                {"name": g["name"], "location": g.get("location")},
            )
            n += 1
        return n

    def _storage_accounts() -> int:
        n = 0
        for a in blob_tools.list_storage_accounts(storage_mgmt_client, rg):
            docstore.apply(
                "blob.create_storage_account",
                {
                    "account_name": a["name"],
                    "resource_group": a.get("resource_group"),
                    "location": a.get("location"),
                    "sku": a.get("sku"),
                },
            )
            n += 1
        return n

    def _sql_servers() -> int:
        n = 0
        for srv in sql_tools.list_sql_servers(sql_mgmt_client):
            docstore.apply(
                "sql.create_sql_server",
                {"server_name": srv["name"], "resource_group": rg, "location": srv.get("location")},
            )
            try:
                for db in sql_tools.list_sql_databases(sql_mgmt_client, rg, srv["name"]):
                    docstore.apply(
                        "sql.create_sql_database",
                        {"server_name": srv["name"], "database_name": db["name"]},
                    )
            except Exception:  # noqa: BLE001
                pass
            n += 1
        return n

    _try("resource_groups", _resource_groups)
    _try("storage_accounts", _storage_accounts)
    _try("sql_servers", _sql_servers)

    # --- ADF (factory configurata) ---
    def _adf() -> dict[str, int]:
        counts = {"linked_services": 0, "datasets": 0, "pipelines": 0}
        for ls in adf_tools.list_linked_services(adf_client, resource_group_default, factory_name):
            docstore.apply(
                "adf.create_linked_service",
                {"name": ls["name"], "service_type": ls.get("type", "unknown")},
            )
            counts["linked_services"] += 1
        for ds in adf_tools.list_datasets(adf_client, resource_group_default, factory_name):
            definition = adf_tools.get_dataset(
                adf_client, resource_group_default, factory_name, ds["name"]
            )
            docstore.apply(
                "adf.create_dataset", {"name": ds["name"], "definition": definition}
            )
            counts["datasets"] += 1
        for pl in adf_tools.list_pipelines(adf_client, resource_group_default, factory_name):
            definition = adf_tools.get_pipeline(
                adf_client, resource_group_default, factory_name, pl["name"]
            )
            docstore.apply(
                "adf.create_pipeline",
                {"pipeline_name": pl["name"], "definition": definition},
            )
            counts["pipelines"] += 1
        return counts

    _try("adf", _adf)

    # --- Networking ---
    def _networking() -> dict[str, int]:
        vnets = {}
        for v in network_tools.list_virtual_networks(network_client, rg):
            subnets = [
                s["name"]
                for s in network_tools.list_subnets(
                    network_client, v.get("resource_group") or rg, v["name"]
                )
            ]
            vnets[v["name"]] = {"address_space": v.get("address_space", []), "subnets": subnets}
        docstore.state["networking"] = {
            "vnets": vnets,
            "private_endpoints": network_tools.list_private_endpoints(network_client, rg),
            "nsgs": network_tools.list_network_security_groups(network_client, rg),
        }
        return {"vnets": len(vnets)}

    _try("networking", _networking)

    # --- Storage data-plane (account di default) ---
    _try(
        "containers",
        lambda: [
            docstore.apply("blob.create_container", {"container": c})
            for c in blob_tools.list_containers(blob_client)
        ]
        and len(blob_tools.list_containers(blob_client)),
    )
    _try(
        "tables",
        lambda: [
            docstore.apply("table.create_table", {"table_name": t})
            for t in table_tools.list_tables(table_service)
        ]
        and len(table_tools.list_tables(table_service)),
    )
    _try(
        "queues",
        lambda: [
            docstore.apply("queue.create_queue", {"queue_name": q})
            for q in queue_tools.list_queues(queue_service)
        ]
        and len(queue_tools.list_queues(queue_service)),
    )
    _try(
        "file_shares",
        lambda: [
            docstore.apply("fileshare.create_share", {"share_name": s})
            for s in fileshare_tools.list_shares(share_service)
        ]
        and len(fileshare_tools.list_shares(share_service)),
    )

    # --- SQL data-plane (connessione di default) ---
    def _sql_tables() -> int:
        tables = [f"dbo.{t}" for t in sql_tools.list_tables(sql_conn)]
        if tables:
            docstore.state["sql_tables"]["default"] = tables
        return len(tables)

    _try("sql_tables_default", _sql_tables)

    docstore.save()
    summary["markdown"] = docstore.markdown_path
    return summary
