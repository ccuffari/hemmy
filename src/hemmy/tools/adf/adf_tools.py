"""Tool per Azure Data Factory (SOLA LETTURA).

Funzioni invocabili dall'agente per leggere i metadati delle pipeline.
Usano il client `DataFactoryManagementClient` costruito in `infra/clients.py`.

Firma comune: (client, resource_group, factory_name, ...args LLM).
Nel loop dell'agente client/resource_group/factory_name vengono "legati" via
functools.partial in `interfaces/cli.py`, quindi il modello passa solo gli
argomenti specifici (es. pipeline_name).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def list_pipelines(client: Any, resource_group: str, factory_name: str) -> list[dict[str, Any]]:
    """Elenca le pipeline della Data Factory (nome + numero di attività)."""
    result = []
    for p in client.pipelines.list_by_factory(resource_group, factory_name):
        result.append({"name": p.name, "activities": len(p.activities or [])})
    return result


def get_pipeline(
    client: Any, resource_group: str, factory_name: str, pipeline_name: str
) -> dict[str, Any]:
    """Restituisce la definizione (attività, parametri) di una pipeline."""
    pipeline = client.pipelines.get(resource_group, factory_name, pipeline_name)
    return pipeline.as_dict()


def get_pipeline_runs(
    client: Any, resource_group: str, factory_name: str, last_days: int = 7
) -> list[dict[str, Any]]:
    """Restituisce le esecuzioni recenti delle pipeline (stato, durata, errori)."""
    from azure.mgmt.datafactory.models import RunFilterParameters

    now = datetime.now(timezone.utc)
    filter_params = RunFilterParameters(
        last_updated_after=now - timedelta(days=last_days),
        last_updated_before=now,
    )
    response = client.pipeline_runs.query_by_factory(
        resource_group, factory_name, filter_params
    )
    runs = []
    for r in response.value:
        runs.append(
            {
                "run_id": r.run_id,
                "pipeline_name": r.pipeline_name,
                "status": r.status,
                "run_start": str(r.run_start),
                "run_end": str(r.run_end),
                "duration_ms": r.duration_in_ms,
                "message": r.message,
            }
        )
    return runs


def get_activity_runs(
    client: Any, resource_group: str, factory_name: str, run_id: str, last_days: int = 7
) -> list[dict[str, Any]]:
    """Restituisce i dettagli delle attività di una specifica esecuzione."""
    from azure.mgmt.datafactory.models import RunFilterParameters

    now = datetime.now(timezone.utc)
    filter_params = RunFilterParameters(
        last_updated_after=now - timedelta(days=last_days),
        last_updated_before=now,
    )
    response = client.activity_runs.query_by_pipeline_run(
        resource_group, factory_name, run_id, filter_params
    )
    activities = []
    for a in response.value:
        activities.append(
            {
                "activity_name": a.activity_name,
                "activity_type": a.activity_type,
                "status": a.status,
                "error": a.error,
                "duration_ms": a.duration_in_ms,
            }
        )
    return activities


def get_triggers(client: Any, resource_group: str, factory_name: str) -> list[dict[str, Any]]:
    """Elenca i trigger e il loro stato di runtime."""
    triggers = []
    for t in client.triggers.list_by_factory(resource_group, factory_name):
        props = t.properties
        triggers.append(
            {
                "name": t.name,
                "type": type(props).__name__ if props else None,
                "runtime_state": getattr(props, "runtime_state", None),
            }
        )
    return triggers


def list_managed_private_endpoints(
    client: Any, resource_group: str, factory_name: str, managed_vnet_name: str = "default"
) -> list[dict[str, Any]]:
    """Elenca i Managed Private Endpoint della Managed VNet di ADF."""
    result = []
    for mpe in client.managed_private_endpoints.list_by_factory(
        resource_group, factory_name, managed_vnet_name
    ):
        p = mpe.properties
        state = getattr(p, "connection_state", None)
        result.append(
            {
                "name": mpe.name,
                "group_id": getattr(p, "group_id", None),
                "private_link_resource_id": getattr(p, "private_link_resource_id", None),
                "status": getattr(state, "status", None),
            }
        )
    return result


def create_managed_vnet(
    client: Any, resource_group: str, factory_name: str, managed_vnet_name: str = "default"
) -> dict[str, Any]:
    """[WRITE] Abilita la Managed Virtual Network sulla Data Factory."""
    from azure.mgmt.datafactory.models import (
        ManagedVirtualNetwork,
        ManagedVirtualNetworkResource,
    )

    resource = ManagedVirtualNetworkResource(properties=ManagedVirtualNetwork())
    result = client.managed_virtual_networks.create_or_update(
        resource_group, factory_name, managed_vnet_name, resource
    )
    return {"managed_vnet": result.name}


def create_managed_private_endpoint(
    client: Any,
    resource_group: str,
    factory_name: str,
    name: str,
    group_id: str,
    private_link_resource_id: str,
    managed_vnet_name: str = "default",
) -> dict[str, Any]:
    """[WRITE] Crea un Managed Private Endpoint verso una risorsa (Blob/SQL/KV...).

    group_id: sub-resource (es. 'blob', 'sqlServer', 'vault').
    private_link_resource_id: resource id della risorsa target.
    Nota: dopo la creazione la connessione va APPROVATA lato risorsa target.
    """
    from azure.mgmt.datafactory.models import ManagedPrivateEndpointResource

    resource = ManagedPrivateEndpointResource.from_dict(
        {
            "properties": {
                "privateLinkResourceId": private_link_resource_id,
                "groupId": group_id,
            }
        }
    )
    result = client.managed_private_endpoints.create_or_update(
        resource_group, factory_name, managed_vnet_name, name, resource
    )
    return {"managed_private_endpoint": result.name, "group_id": group_id}


def get_factory_identity(
    client: Any, resource_group: str, factory_name: str
) -> dict[str, Any]:
    """Restituisce la Managed Identity della Data Factory (principal_id da usare in RBAC)."""
    factory = client.factories.get(resource_group, factory_name)
    identity = factory.identity
    return {
        "factory": factory_name,
        "principal_id": getattr(identity, "principal_id", None),
        "tenant_id": getattr(identity, "tenant_id", None),
        "type": getattr(identity, "type", None),
    }


def list_linked_services(
    client: Any, resource_group: str, factory_name: str
) -> list[dict[str, Any]]:
    """Elenca i Linked Service (nome + tipo)."""
    result = []
    for ls in client.linked_services.list_by_factory(resource_group, factory_name):
        result.append({"name": ls.name, "type": type(ls.properties).__name__})
    return result


def get_linked_service(
    client: Any, resource_group: str, factory_name: str, name: str
) -> dict[str, Any]:
    """Restituisce la definizione di un Linked Service.

    Le connection string sono SecureString: ADF NON restituisce il valore in chiaro
    (compare solo il tipo). Utile per verificare il TIPO del LS e la configurazione.
    """
    ls = client.linked_services.get(resource_group, factory_name, name)
    return ls.as_dict()


def list_datasets(
    client: Any, resource_group: str, factory_name: str
) -> list[dict[str, Any]]:
    """Elenca i Dataset (nome + tipo)."""
    result = []
    for d in client.datasets.list_by_factory(resource_group, factory_name):
        result.append({"name": d.name, "type": type(d.properties).__name__})
    return result


def get_dataset(
    client: Any, resource_group: str, factory_name: str, name: str
) -> dict[str, Any]:
    """Restituisce la definizione di un Dataset, incluso il Linked Service referenziato.

    Serve per risalire dal dataset (es. sink di una pipeline) al Linked Service usato.
    """
    d = client.datasets.get(resource_group, factory_name, name)
    return d.as_dict()


# ===================================================================== WRITE
# ATTENZIONE: le funzioni seguenti MODIFICANO risorse Azure Data Factory.
# Sono registrate come azioni di scrittura e richiedono approvazione umana
# tramite il guardrail (human-in-the-loop) prima di essere eseguite.


def create_linked_service(
    client: Any,
    resource_group: str,
    factory_name: str,
    secret_provider: Any,
    name: str,
    service_type: str,
) -> dict[str, Any]:
    """Crea/aggiorna un Linked Service (connessione a Blob o SQL) in modo sicuro.

    Il modello fornisce solo `name` e `service_type`; la connection string viene
    chiesta all'operatore (man-in-the-middle), inserita in un SecureString e inviata
    ad ADF. Il segreto NON viene mai passato o restituito al modello LLM.

    service_type: "AzureBlobStorage" | "AzureSqlDatabase".
    """
    connection_string = secret_provider(
        f"connection string per il Linked Service '{name}' ({service_type})"
    )
    if not connection_string:
        raise ValueError(
            "Nessuna connection string fornita dall'operatore: operazione annullata."
        )

    from azure.mgmt.datafactory.models import (
        AzureBlobStorageLinkedService,
        AzureSqlDatabaseLinkedService,
        LinkedServiceResource,
        SecureString,
    )

    secure = SecureString(value=connection_string)
    if service_type == "AzureBlobStorage":
        linked_service = AzureBlobStorageLinkedService(connection_string=secure)
    elif service_type == "AzureSqlDatabase":
        linked_service = AzureSqlDatabaseLinkedService(connection_string=secure)
    else:
        raise ValueError(
            f"Tipo Linked Service non supportato in modo sicuro: '{service_type}'. "
            "Supportati: AzureBlobStorage, AzureSqlDatabase."
        )

    resource = LinkedServiceResource(properties=linked_service)
    result = client.linked_services.create_or_update(
        resource_group, factory_name, name, resource
    )
    return {"linked_service": result.name, "etag": result.etag, "type": service_type}


def create_keyvault_linked_service(
    client: Any, resource_group: str, factory_name: str, name: str, vault_url: str
) -> dict[str, Any]:
    """[WRITE] Crea un Linked Service di tipo AzureKeyVault.

    ADF vi accede tramite la propria Managed Identity: nessun segreto qui.
    `vault_url` es. https://<vault>.vault.azure.net.
    """
    from azure.mgmt.datafactory.models import (
        AzureKeyVaultLinkedService,
        LinkedServiceResource,
    )

    if not vault_url.startswith("http"):
        vault_url = f"https://{vault_url}.vault.azure.net"
    ls = AzureKeyVaultLinkedService(base_url=vault_url)
    result = client.linked_services.create_or_update(
        resource_group, factory_name, name, LinkedServiceResource(properties=ls)
    )
    return {"linked_service": result.name, "type": "AzureKeyVault", "vault_url": vault_url}


def create_linked_service_kv(
    client: Any,
    resource_group: str,
    factory_name: str,
    name: str,
    service_type: str,
    kv_linked_service: str,
    secret_name: str,
) -> dict[str, Any]:
    """[WRITE] Crea un Linked Service (Blob/SQL) con connection string da Key Vault.

    Nessun segreto passa dall'operatore né dal modello: la connection string è un
    RIFERIMENTO al secret `secret_name` nel Key Vault linked service `kv_linked_service`.
    service_type: "AzureBlobStorage" | "AzureSqlDatabase".
    """
    from azure.mgmt.datafactory.models import (
        AzureBlobStorageLinkedService,
        AzureKeyVaultSecretReference,
        AzureSqlDatabaseLinkedService,
        LinkedServiceReference,
        LinkedServiceResource,
    )

    secret_ref = AzureKeyVaultSecretReference(
        store=LinkedServiceReference(reference_name=kv_linked_service, type="LinkedServiceReference"),
        secret_name=secret_name,
    )
    if service_type == "AzureBlobStorage":
        ls = AzureBlobStorageLinkedService(connection_string=secret_ref)
    elif service_type == "AzureSqlDatabase":
        ls = AzureSqlDatabaseLinkedService(connection_string=secret_ref)
    else:
        raise ValueError(
            f"Tipo non supportato: '{service_type}'. Usa AzureBlobStorage o AzureSqlDatabase."
        )
    result = client.linked_services.create_or_update(
        resource_group, factory_name, name, LinkedServiceResource(properties=ls)
    )
    return {
        "linked_service": result.name,
        "type": service_type,
        "kv_linked_service": kv_linked_service,
        "secret_name": secret_name,
    }


def create_dataset(
    client: Any, resource_group: str, factory_name: str, name: str, definition: dict[str, Any]
) -> dict[str, Any]:
    """Crea/aggiorna un Dataset (sorgente o destinazione).

    `definition` = JSON del DatasetResource (properties con "type", "linkedServiceName",
    "schema"/"tableName" o "location").
    """
    from azure.mgmt.datafactory.models import DatasetResource

    resource = DatasetResource.from_dict(definition)
    result = client.datasets.create_or_update(resource_group, factory_name, name, resource)
    return {"dataset": result.name, "etag": result.etag}


def create_pipeline(
    client: Any,
    resource_group: str,
    factory_name: str,
    pipeline_name: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Crea/aggiorna una pipeline.

    `definition` = JSON del PipelineResource (contiene "properties" -> "activities",
    tipicamente una Copy Activity con "source"/"sink" e "translator" per il mapping).
    """
    from azure.mgmt.datafactory.models import PipelineResource

    resource = PipelineResource.from_dict(definition)
    result = client.pipelines.create_or_update(
        resource_group, factory_name, pipeline_name, resource
    )
    return {"pipeline": result.name, "etag": result.etag}


def trigger_pipeline_run(
    client: Any,
    resource_group: str,
    factory_name: str,
    pipeline_name: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Avvia manualmente l'esecuzione di una pipeline."""
    run = client.pipelines.create_run(
        resource_group, factory_name, pipeline_name, parameters=parameters or {}
    )
    return {"run_id": run.run_id, "pipeline": pipeline_name}


def run_and_wait(
    client: Any,
    resource_group: str,
    factory_name: str,
    pipeline_name: str,
    parameters: dict[str, Any] | None = None,
    timeout_seconds: int = 300,
    poll_interval: int = 10,
) -> dict[str, Any]:
    """[WRITE] Avvia una pipeline e ATTENDE l'esito, riportando gli errori di attività.

    Fa polling dello stato fino a Succeeded/Failed/Cancelled o al timeout. In caso di
    fallimento raccoglie gli errori delle attività fallite.
    """
    import time

    run = client.pipelines.create_run(
        resource_group, factory_name, pipeline_name, parameters=parameters or {}
    )
    run_id = run.run_id
    terminal = {"Succeeded", "Failed", "Cancelled"}
    deadline = time.time() + timeout_seconds

    info = None
    while True:
        info = client.pipeline_runs.get(resource_group, factory_name, run_id)
        if info.status in terminal or time.time() >= deadline:
            break
        time.sleep(poll_interval)

    result: dict[str, Any] = {
        "run_id": run_id,
        "pipeline": pipeline_name,
        "status": info.status,
        "duration_ms": info.duration_in_ms,
        "message": info.message,
        "timed_out": info.status not in terminal,
    }

    if info.status == "Failed":
        from datetime import datetime, timedelta, timezone

        from azure.mgmt.datafactory.models import RunFilterParameters

        now = datetime.now(timezone.utc)
        filter_params = RunFilterParameters(
            last_updated_after=now - timedelta(days=1),
            last_updated_before=now + timedelta(minutes=5),
        )
        acts = client.activity_runs.query_by_pipeline_run(
            resource_group, factory_name, run_id, filter_params
        )
        result["failed_activities"] = [
            {"activity": a.activity_name, "type": a.activity_type, "error": a.error}
            for a in acts.value
            if a.status == "Failed"
        ]
    return result


def delete_linked_service(
    client: Any, resource_group: str, factory_name: str, name: str
) -> dict[str, Any]:
    """[WRITE] Elimina un Linked Service."""
    client.linked_services.delete(resource_group, factory_name, name)
    return {"deleted_linked_service": name}


def delete_dataset(
    client: Any, resource_group: str, factory_name: str, name: str
) -> dict[str, Any]:
    """[WRITE] Elimina un Dataset."""
    client.datasets.delete(resource_group, factory_name, name)
    return {"deleted_dataset": name}


def delete_pipeline(
    client: Any, resource_group: str, factory_name: str, pipeline_name: str
) -> dict[str, Any]:
    """[WRITE] Elimina una pipeline."""
    client.pipelines.delete(resource_group, factory_name, pipeline_name)
    return {"deleted_pipeline": pipeline_name}


def _ls_ref(name: str) -> dict[str, Any]:
    return {"referenceName": name, "type": "LinkedServiceReference"}


def _ds_ref(name: str) -> dict[str, Any]:
    return {"referenceName": name, "type": "DatasetReference"}


def _build_source_dataset(
    linked_service: str, container: str, file_name: str, fmt: str, sheet: str | None
) -> dict[str, Any]:
    """Costruisce la definizione del dataset sorgente (file su Blob)."""
    location = {
        "type": "AzureBlobStorageLocation",
        "container": container,
        "fileName": file_name,
    }
    type_props: dict[str, Any] = {"location": location}
    if fmt == "Excel":
        type_props["sheetName"] = sheet or "Sheet1"
        type_props["firstRowAsHeader"] = True
    elif fmt == "DelimitedText":
        type_props["firstRowAsHeader"] = True
        type_props["columnDelimiter"] = ","
    # Json / Parquet: solo location
    return {"properties": {"type": fmt, "linkedServiceName": _ls_ref(linked_service), "typeProperties": type_props}}


def _build_sink_dataset(linked_service: str, schema: str, table: str) -> dict[str, Any]:
    """Costruisce la definizione del dataset destinazione (tabella SQL)."""
    return {
        "properties": {
            "type": "AzureSqlTable",
            "linkedServiceName": _ls_ref(linked_service),
            "typeProperties": {"schema": schema, "table": table},
        }
    }


def create_copy_pipeline(
    client: Any,
    resource_group: str,
    factory_name: str,
    pipeline_name: str,
    source_linked_service: str,
    source_container: str,
    source_file: str,
    sink_linked_service: str,
    sink_table: str,
    column_mappings: list[dict[str, str]],
    source_format: str = "DelimitedText",
    source_sheet: str | None = None,
    sink_schema: str = "dbo",
    source_dataset_name: str | None = None,
    sink_dataset_name: str | None = None,
) -> dict[str, Any]:
    """[WRITE] Crea in un colpo solo i dataset e la pipeline di copia Blob(file) -> SQL.

    Assembla internamente il JSON corretto (dataset sorgente, dataset destinazione,
    pipeline con Copy Activity + TabularTranslator per mapping/cast). Il modello passa
    solo parametri semplici.

    - source_format: "DelimitedText" (CSV) | "Excel" | "Json" | "Parquet".
    - column_mappings: lista di {"source": str, "sink": str, "type": str (opz)}.
      `type` = tipo destinazione per il cast (es. "Int32", "String", "DateTime").
    - I Linked Service (source/sink) devono già esistere (creali prima con
      adf.create_linked_service).
    """
    from azure.mgmt.datafactory.models import DatasetResource, PipelineResource

    src_ds = source_dataset_name or f"DS_src_{pipeline_name}"
    sink_ds = sink_dataset_name or f"DS_sink_{pipeline_name}"

    # 1) Dataset sorgente e destinazione
    client.datasets.create_or_update(
        resource_group,
        factory_name,
        src_ds,
        DatasetResource.from_dict(
            _build_source_dataset(source_linked_service, source_container, source_file, source_format, source_sheet)
        ),
    )
    client.datasets.create_or_update(
        resource_group,
        factory_name,
        sink_ds,
        DatasetResource.from_dict(_build_sink_dataset(sink_linked_service, sink_schema, sink_table)),
    )

    # 2) Translator: mapping colonne + cast di tipo
    mappings = []
    for m in column_mappings:
        entry: dict[str, Any] = {"source": {"name": m["source"]}, "sink": {"name": m["sink"]}}
        if m.get("type"):
            entry["sink"]["type"] = m["type"]
        mappings.append(entry)

    translator = {
        "type": "TabularTranslator",
        "mappings": mappings,
        "typeConversion": True,
        "typeConversionSettings": {"allowDataTruncation": True},
    }

    # 3) Pipeline con Copy Activity
    pipeline_def = {
        "properties": {
            "activities": [
                {
                    "name": "CopyBlobToSql",
                    "type": "Copy",
                    "inputs": [_ds_ref(src_ds)],
                    "outputs": [_ds_ref(sink_ds)],
                    "typeProperties": {
                        "source": {
                            "type": f"{source_format}Source",
                            "storeSettings": {"type": "AzureBlobStorageReadSettings", "recursive": False},
                        },
                        "sink": {"type": "AzureSqlSink"},
                        "translator": translator,
                    },
                }
            ]
        }
    }
    result = client.pipelines.create_or_update(
        resource_group, factory_name, pipeline_name, PipelineResource.from_dict(pipeline_def)
    )

    return {
        "created_pipeline": result.name,
        "source_dataset": src_ds,
        "sink_dataset": sink_ds,
        "mappings": [f"{m['source']}->{m['sink']}" + (f":{m['type']}" if m.get("type") else "") for m in column_mappings],
    }
