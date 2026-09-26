"""Tool per Azure Blob Storage (SOLA LETTURA).

Funzioni per ispezionare i file sorgente e inferirne lo schema.
Usano il `BlobServiceClient` costruito in `infra/clients.py`.
Supporta inferenza schema per CSV e JSON (righe JSON o array).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

# Byte massimi scaricati per l'inferenza dello schema (campione).
_SAMPLE_BYTES = 64 * 1024


def list_storage_accounts(
    mgmt_client: Any, resource_group: str | None = None
) -> list[dict[str, Any]]:
    """Elenca gli storage account (management-plane).

    Se `resource_group` è fornito, filtra su quel gruppo; altrimenti elenca tutti
    gli account della subscription. Richiede il client StorageManagementClient.
    """
    if resource_group:
        accounts = mgmt_client.storage_accounts.list_by_resource_group(resource_group)
    else:
        accounts = mgmt_client.storage_accounts.list()

    result = []
    for a in accounts:
        # a.id: /subscriptions/<sub>/resourceGroups/<rg>/providers/.../<name>
        parts = a.id.split("/")
        rg = parts[4] if len(parts) > 4 else None
        result.append(
            {
                "name": a.name,
                "resource_group": rg,
                "location": a.location,
                "kind": a.kind,
                "sku": a.sku.name if a.sku else None,
            }
        )
    return result


def create_storage_account(
    mgmt_client: Any,
    resource_group: str,
    account_name: str,
    location: str,
    sku: str = "Standard_LRS",
    kind: str = "StorageV2",
) -> dict[str, Any]:
    """[WRITE] Crea uno storage account (management-plane).

    account_name: 3-24 caratteri, solo minuscole/numeri, univoco a livello globale.
    """
    from azure.mgmt.storage.models import Sku, StorageAccountCreateParameters

    poller = mgmt_client.storage_accounts.begin_create(
        resource_group,
        account_name,
        StorageAccountCreateParameters(sku=Sku(name=sku), kind=kind, location=location),
    )
    account = poller.result()
    return {
        "created_storage_account": account.name,
        "resource_group": resource_group,
        "location": account.location,
        "sku": account.sku.name if account.sku else sku,
    }


def list_containers(client: Any) -> list[str]:
    """Elenca i container dello storage account configurato (data-plane)."""
    return [c.name for c in client.list_containers()]


# --------------------------------------------- Accesso a un account ARBITRARIO
# La connection string viene richiesta all'operatore al momento (man-in-the-middle)
# tramite `secret_provider` e NON viene mai restituita, loggata o passata al LLM.
# `secret_provider(label)` -> str: prompt sicuro (es. utils.helpers.prompt_secret).


def _client_for_account(secret_provider: Any, account_name: str) -> Any:
    """Costruisce un BlobServiceClient transitorio chiedendo la connection string.

    Il segreto resta locale a questa funzione: non viene restituito al chiamante.
    """
    from azure.storage.blob import BlobServiceClient

    connection_string = secret_provider(
        f"connection string per lo storage account '{account_name}'"
    )
    if not connection_string:
        raise ValueError(
            "Nessuna connection string fornita dall'operatore: operazione annullata."
        )
    return BlobServiceClient.from_connection_string(connection_string)


def list_containers_for_account(secret_provider: Any, account_name: str) -> list[str]:
    """Elenca i container di uno storage account arbitrario (connection string sicura)."""
    client = _client_for_account(secret_provider, account_name)
    return list_containers(client)


def list_blobs_for_account(
    secret_provider: Any, account_name: str, container: str, prefix: str = ""
) -> list[dict[str, Any]]:
    """Elenca i blob di un container in uno storage account arbitrario."""
    client = _client_for_account(secret_provider, account_name)
    return list_blobs(client, container, prefix)


def get_blob_schema_for_account(
    secret_provider: Any, account_name: str, container: str, blob_name: str
) -> dict[str, Any]:
    """Inferisce lo schema di un file in uno storage account arbitrario."""
    client = _client_for_account(secret_provider, account_name)
    return get_blob_schema(client, container, blob_name)


def list_blobs(client: Any, container: str, prefix: str = "") -> list[dict[str, Any]]:
    """Elenca i blob in un container (nome, dimensione, ultima modifica)."""
    container_client = client.get_container_client(container)
    blobs = []
    for b in container_client.list_blobs(name_starts_with=prefix):
        blobs.append(
            {
                "name": b.name,
                "size_bytes": b.size,
                "last_modified": str(b.last_modified),
            }
        )
    return blobs


def get_blob_schema(client: Any, container: str, blob_name: str) -> dict[str, Any]:
    """Inferisce lo schema (colonne + tipo indicativo) di un file sorgente.

    Scarica solo un campione iniziale. Gestisce CSV e JSON.
    """
    blob_client = client.get_blob_client(container=container, blob=blob_name)
    downloader = blob_client.download_blob(offset=0, length=_SAMPLE_BYTES)
    sample = downloader.readall().decode("utf-8", errors="replace")

    lower = blob_name.lower()
    if lower.endswith(".csv"):
        return _infer_csv_schema(sample, blob_name)
    if lower.endswith(".json") or lower.endswith(".jsonl") or lower.endswith(".ndjson"):
        return _infer_json_schema(sample, blob_name)
    return {"blob": blob_name, "format": "unknown", "note": "formato non supportato per inferenza"}


def _infer_csv_schema(sample: str, blob_name: str) -> dict[str, Any]:
    reader = csv.reader(io.StringIO(sample))
    rows = [r for _, r in zip(range(50), reader)]
    if not rows:
        return {"blob": blob_name, "format": "csv", "columns": []}
    header = rows[0]
    columns = [{"name": col, "type": "string"} for col in header]
    return {"blob": blob_name, "format": "csv", "columns": columns}


def _infer_json_schema(sample: str, blob_name: str) -> dict[str, Any]:
    record: dict[str, Any] | None = None
    stripped = sample.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list) and parsed:
            record = parsed[0]
        elif isinstance(parsed, dict):
            record = parsed
    except json.JSONDecodeError:
        # JSON Lines: prende la prima riga completa
        first_line = stripped.splitlines()[0] if stripped else ""
        try:
            record = json.loads(first_line)
        except json.JSONDecodeError:
            record = None

    if not isinstance(record, dict):
        return {"blob": blob_name, "format": "json", "columns": [], "note": "campione non parsabile"}

    columns = [{"name": k, "type": type(v).__name__} for k, v in record.items()]
    return {"blob": blob_name, "format": "json", "columns": columns}


# ===================================================================== WRITE
# ATTENZIONE: carica/sovrascrive un blob. Azione di scrittura: richiede
# approvazione umana (guardrail).


def create_container(client: Any, container: str) -> dict[str, Any]:
    """Crea un container nello storage account configurato."""
    client.create_container(container)
    return {"created_container": container}


def delete_container(client: Any, container: str) -> dict[str, Any]:
    """Elimina un container (e tutto il suo contenuto) nello storage configurato."""
    client.delete_container(container)
    return {"deleted_container": container}


def upload(client: Any, container: str, blob_name: str, content: str) -> dict[str, Any]:
    """Carica (o sovrascrive) un blob con contenuto testuale."""
    blob_client = client.get_blob_client(container=container, blob=blob_name)
    data = content.encode("utf-8") if isinstance(content, str) else content
    blob_client.upload_blob(data, overwrite=True)
    return {"uploaded": f"{container}/{blob_name}", "size_bytes": len(data)}
