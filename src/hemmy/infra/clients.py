"""Client SDK: ADF, Blob, SQL + autenticazione.

Centralizza la costruzione dei client verso i servizi Azure, leggendo le
credenziali dalle variabili d'ambiente (caricate da .env).

Autenticazione:
- Azure Data Factory: Service Principal via `azure-identity` (ClientSecretCredential).
- Blob Storage: connection string.
- SQL Database: connection string (pyodbc).

STUB: costruttori dei client; completare con parametri e gestione errori.
"""

from __future__ import annotations

import os
from typing import Any, Callable


class LazyClient:
    """Proxy che costruisce il client sottostante solo al primo utilizzo (lazy).

    Evita connessioni "a priori" all'avvio: ogni tool si collega al proprio servizio
    solo quando viene effettivamente usato, e il client viene poi riusato.
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_obj", None)

    def _resolve(self) -> Any:
        if object.__getattribute__(self, "_obj") is None:
            object.__setattr__(self, "_obj", object.__getattribute__(self, "_factory")())
        return object.__getattribute__(self, "_obj")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


def _credential() -> Any:
    """Credenziale Azure.

    Default: identità PERSONALE dell'utente che esegue l'agente (Azure CLI, poi browser
    interattivo). Così l'agente eredita permessi e responsabilità del singolo utente
    (architect/engineer/analyst) e i fallimenti RBAC sono corretti e voluti.

    AZURE_AUTH_MODE=service_principal forza l'uso dello SP (utile per la CI/automazione).
    """
    # Se l'utente attualmente legato all'agente ha completato il login OAuth
    # per-utente (bottone "Connetti Azure" nella UI web, redirect al portale
    # Microsoft), usiamo QUELLA identità: eredita esattamente i suoi permessi,
    # non quelli di un Service Principal condiviso.
    try:
        from hemmy.auth.oauth_providers import get_current_azure_credential

        user_cred = get_current_azure_credential()
        if user_cred is not None:
            return user_cred
    except Exception:  # noqa: BLE001
        pass

    mode = os.getenv("AZURE_AUTH_MODE", "user").lower()
    if mode == "service_principal":
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )

    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        InteractiveBrowserCredential,
    )

    tenant = os.getenv("AZURE_TENANT_ID")
    interactive = (
        InteractiveBrowserCredential(tenant_id=tenant)
        if tenant
        else InteractiveBrowserCredential()
    )
    # Prima prova l'identità di 'az login', altrimenti apre il browser.
    return ChainedTokenCredential(AzureCliCredential(), interactive)


def get_credential() -> Any:
    """Credenziale Service Principal (usata da data-plane come Key Vault)."""
    return _credential()


_ARM_SCOPE = "https://management.azure.com/.default"


def get_arm_bearer_token(scope: str = _ARM_SCOPE) -> str:
    """Bearer token ARM (Azure Resource Manager) tramite lo stesso choke-point di
    `_credential()`: identità OAuth per-utente se collegata, altrimenti la CLI/il
    browser dell'operatore, altrimenti (solo se esplicitamente configurato) il
    Service Principal condiviso.

    Introdotta per i plugin (`native_plugins/`, `plugins/users/<id>/`) che prima
    facevano un fetch OAuth2 client-credentials "a mano" leggendo direttamente
    ARM_TENANT_ID/ARM_CLIENT_ID/ARM_CLIENT_SECRET — bypassando completamente il
    login OAuth per-utente. Ora anche loro ereditano l'identità dell'utente.
    """
    return _credential().get_token(scope).token


def get_keyvault_mgmt_client() -> Any:
    """Client management-plane per elencare/gestire i Key Vault."""
    from azure.mgmt.keyvault import KeyVaultManagementClient

    return KeyVaultManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_authorization_client() -> Any:
    """Client management-plane per gestire i role assignment (RBAC)."""
    from azure.mgmt.authorization import AuthorizationManagementClient

    return AuthorizationManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_network_client() -> Any:
    """Client management-plane per la rete (VNet, subnet, private endpoint, NSG)."""
    from azure.mgmt.network import NetworkManagementClient

    return NetworkManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_lock_client() -> Any:
    """Client management-plane per gli Azure Management Lock (CanNotDelete/ReadOnly)."""
    try:
        from azure.mgmt.resource.locks import ManagementLockClient
    except ImportError as exc:
        raise ImportError(
            "Il client Management Lock non è disponibile: dalla 24.x il rollup "
            "'azure-mgmt-resource' non include più 'locks'. Installa una versione "
            "compatibile: python -m pip install 'azure-mgmt-resource>=23.0.0,<24.0.0'."
        ) from exc

    return ManagementLockClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_adf_client() -> Any:
    """Crea il client di management di Azure Data Factory con l'identità personale."""
    from azure.mgmt.datafactory import DataFactoryManagementClient

    return DataFactoryManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_blob_client() -> Any:
    """Crea il client (data-plane) di Azure Blob Storage dalla connection string.

    Opera su UN solo storage account (quello della connection string): elenca
    container e blob, non gli storage account.
    """
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient.from_connection_string(
        os.environ["BLOB_CONNECTION_STRING"]
    )


def _storage_connection_string() -> str:
    """Connection string dell'account di storage predefinito.

    È una connection string a livello di account (AccountName + AccountKey), valida
    per tutti i servizi: Blob, Table, Queue, File.
    """
    return os.environ["BLOB_CONNECTION_STRING"]


def get_table_client() -> Any:
    """Crea il client (data-plane) di Azure Table Storage."""
    from azure.data.tables import TableServiceClient

    return TableServiceClient.from_connection_string(_storage_connection_string())


def get_queue_client() -> Any:
    """Crea il client (data-plane) di Azure Queue Storage."""
    from azure.storage.queue import QueueServiceClient

    return QueueServiceClient.from_connection_string(_storage_connection_string())


def get_share_client() -> Any:
    """Crea il client (data-plane) di Azure File Share."""
    from azure.storage.fileshare import ShareServiceClient

    return ShareServiceClient.from_connection_string(_storage_connection_string())


def get_storage_mgmt_client() -> Any:
    """Crea il client (management-plane) per enumerare gli storage account.

    Usa il Service Principal e ADF_SUBSCRIPTION_ID. Serve per elencare gli account
    di storage della subscription (operazione non disponibile via data-plane).
    """
    from azure.mgmt.storage import StorageManagementClient

    return StorageManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_sql_mgmt_client() -> Any:
    """Crea il client (management-plane) per gestire SQL Server e database.

    Serve per elencare/creare server e database SQL (operazioni non disponibili
    via data-plane pyodbc).
    """
    from azure.mgmt.sql import SqlManagementClient

    return SqlManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_resource_mgmt_client() -> Any:
    """Crea il client (management-plane) per gestire i resource group."""
    try:
        from azure.mgmt.resource import ResourceManagementClient
    except ImportError:
        # Fallback su alcune versioni/installazioni del pacchetto.
        try:
            from azure.mgmt.resource.resources import ResourceManagementClient
        except ImportError as exc:
            raise ImportError(
                "Pacchetto 'azure-mgmt-resource' non installato correttamente. "
                "Esegui: python -m pip install --force-reinstall azure-mgmt-resource "
                "(usando lo stesso interprete che avvia l'app)."
            ) from exc

    return ResourceManagementClient(
        credential=_credential(),
        subscription_id=os.environ["ADF_SUBSCRIPTION_ID"],
    )


def get_sql_connection() -> Any:
    """Apre una connessione ad Azure SQL Database via pyodbc.

    TODO: gestire il driver ODBC e il pooling. Usa SQL_CONNECTION_STRING.
    """
    import pyodbc

    return pyodbc.connect(os.environ["SQL_CONNECTION_STRING"])
