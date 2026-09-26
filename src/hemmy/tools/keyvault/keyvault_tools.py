"""Tool Azure Key Vault — segreti centralizzati.

L'operatore inserisce il valore del segreto UNA VOLTA (input sicuro) e viene salvato
in Key Vault. Da lì in poi ADF e le connessioni usano RIFERIMENTI al segreto: il
valore non viene mai passato al modello LLM né restituito.

- list_vaults / list_secrets: lettura (solo nomi, mai valori).
- set_secret: scrittura (approvazione + valore fornito dall'operatore).
- delete_secret: scrittura (approvazione).
"""

from __future__ import annotations

from typing import Any


def _vault_url(vault: str) -> str:
    if vault.startswith("http://") or vault.startswith("https://"):
        return vault
    return f"https://{vault}.vault.azure.net"


def _secret_client(credential: Any, vault: str) -> Any:
    from azure.keyvault.secrets import SecretClient

    return SecretClient(vault_url=_vault_url(vault), credential=credential)


def list_vaults(mgmt_client: Any) -> list[dict[str, Any]]:
    """Elenca i Key Vault della subscription (management-plane)."""
    vaults = []
    for v in mgmt_client.vaults.list():
        vaults.append({"name": v.name, "id": getattr(v, "id", None)})
    return vaults


def list_secrets(credential: Any, vault: str) -> list[str]:
    """Elenca i NOMI dei segreti in un Key Vault (mai i valori)."""
    client = _secret_client(credential, vault)
    return [p.name for p in client.list_properties_of_secrets()]


def set_secret(
    credential: Any, secret_provider: Any, vault: str, secret_name: str
) -> dict[str, Any]:
    """[WRITE] Salva un segreto in Key Vault.

    Il valore viene chiesto all'operatore in modo sicuro (man-in-the-middle) e NON è
    mai esposto al modello: qui si registra solo l'esito (vault + nome).
    """
    value = secret_provider(f"valore del secret '{secret_name}' per il Key Vault '{vault}'")
    if not value:
        raise ValueError("Nessun valore fornito dall'operatore: operazione annullata.")
    client = _secret_client(credential, vault)
    result = client.set_secret(secret_name, value)
    return {"vault": vault, "secret": secret_name, "version": result.properties.version}


def delete_secret(credential: Any, vault: str, secret_name: str) -> dict[str, Any]:
    """[WRITE] Elimina (soft-delete) un segreto dal Key Vault."""
    client = _secret_client(credential, vault)
    client.begin_delete_secret(secret_name).wait()
    return {"vault": vault, "deleted_secret": secret_name}
