"""Feasibility analysis dell'architettura richiesta (il "cervello" pre-provisioning).

Prima di generare Terraform, l'agente deve verificare se ciò che l'utente chiede è
*tecnicamente coerente* col modello del provider. Esempio classico: "metti tutte le
risorse nella stessa subnet" NON è realizzabile alla lettera per ogni tipo di risorsa
(ADF e Log Analytics non si "iniettano" in una subnet come una VM).

Questo modulo NON tocca Azure: è una knowledge base deterministica per tipo di risorsa
+ una valutazione della postura di rete richiesta. Restituisce, per ogni risorsa:
- se è integrabile con service endpoint su subnet,
- se supporta Private Endpoint,
- se il public access è disabilitabile,
- se supporta VNet injection (delegated subnet),
- il modello di rete corretto,
- e un verdetto sulla realizzabilità della richiesta letterale.
"""

from __future__ import annotations

from typing import Any

# Capacità di rete per tipo di risorsa (PaaS Azure).
#   service_endpoint  : può essere ammessa via VNet service endpoint + network ACL su subnet
#   private_endpoint  : supporta Azure Private Endpoint (private link)
#   disable_public    : il traffico pubblico può essere davvero disattivato
#   vnet_injection    : "vive" dentro una subnet delegata (come una VM/VMSS/SSIS-IR)
#   model             : descrizione sintetica del modello di rete corretto
_CAP: dict[str, dict[str, Any]] = {
    "storage_account": {
        "service_endpoint": True, "private_endpoint": True, "disable_public": True,
        "vnet_injection": False,
        "model": "Service endpoint (Microsoft.Storage) + network_rules default_action=Deny, oppure Private Endpoint per blob/file/…",
    },
    "key_vault": {
        "service_endpoint": True, "private_endpoint": True, "disable_public": True,
        "vnet_injection": False,
        "model": "Service endpoint (Microsoft.KeyVault) + network_acls default_action=Deny, oppure Private Endpoint",
    },
    "sql_server": {
        "service_endpoint": True, "private_endpoint": True, "disable_public": True,
        "vnet_injection": False,
        "model": "VNet rule (service endpoint Microsoft.Sql) + public_network_access_enabled=false, oppure Private Endpoint",
    },
    "sql_database": {
        "service_endpoint": True, "private_endpoint": True, "disable_public": True,
        "vnet_injection": False,
        "model": "Eredita la rete dal SQL Server (nessuna rete propria)",
    },
    "data_factory": {
        "service_endpoint": False, "private_endpoint": True, "disable_public": True,
        "vnet_injection": False,
        "model": "Managed VNet + Managed Private Endpoint (outbound) e/o Private Endpoint inbound; NON si mette in una subnet condivisa",
    },
    "log_analytics": {
        "service_endpoint": False, "private_endpoint": True, "disable_public": True,
        "vnet_injection": False,
        "model": "Azure Monitor Private Link Scope (AMPLS) + Private Endpoint; NON supporta service endpoint né subnet diretta",
    },
    "virtual_machine": {
        "service_endpoint": True, "private_endpoint": False, "disable_public": True,
        "vnet_injection": True,
        "model": "NIC dentro la subnet (vera VNet injection)",
    },
    "container_app_environment": {
        "service_endpoint": False, "private_endpoint": False, "disable_public": True,
        "vnet_injection": True,
        "model": "VNet injection su subnet delegata",
    },
}

# Sinonimi comuni → chiave canonica.
_ALIASES = {
    "storage": "storage_account", "sa": "storage_account", "blob": "storage_account",
    "kv": "key_vault", "keyvault": "key_vault", "vault": "key_vault",
    "sql": "sql_server", "mssql": "sql_server", "sqlserver": "sql_server",
    "sqldb": "sql_database", "database": "sql_database",
    "adf": "data_factory", "datafactory": "data_factory", "factory": "data_factory",
    "log": "log_analytics", "loganalytics": "log_analytics", "workspace": "log_analytics",
    "vm": "virtual_machine",
}


def _canon(resource_type: str) -> str:
    r = (resource_type or "").strip().lower().replace("-", "_")
    return _ALIASES.get(r, r)


def _action_needed(key: str, posture: str, obs: dict[str, Any]) -> str:
    """Confronta lo stato reale osservato (obs) col modello corretto → azione concreta.

    obs: fatti già rilevati dai tool di lettura per QUELLA risorsa, es.
        {"managed_vnet_enabled": false, "public_network_access_enabled": true,
         "private_endpoint_exists": false}.
    """
    if not isinstance(obs, dict):
        return "Stato osservato non interpretabile (atteso un oggetto di fatti)."
    vnet_posture = posture in ("vnet_only", "all_in_one_subnet", "private_endpoint")
    actions: list[str] = []

    if key == "data_factory" and vnet_posture:
        if not obs.get("managed_vnet_enabled", False):
            actions.append(
                "Abilita la Managed VNet (adf.create_managed_vnet), poi crea i Managed "
                "Private Endpoint verso le risorse target."
            )
        else:
            actions.append("Managed VNet già attiva: crea/verifica i Managed Private Endpoint.")

    if vnet_posture and obs.get("public_network_access_enabled") is True:
        actions.append(
            "Accesso pubblico ANCORA attivo: imposta public_network_access_enabled=false "
            "(un firewall deny-all non equivale a disabilitare il public access)."
        )

    if vnet_posture and key in ("storage_account", "key_vault", "sql_server"):
        if obs.get("private_endpoint_exists") or obs.get("service_endpoint_configured"):
            actions.append("Rete privata già configurata: nessuna azione strutturale.")
        elif "private_endpoint_exists" in obs or "service_endpoint_configured" in obs:
            actions.append(
                "Rete privata NON ancora configurata: aggiungi service endpoint + network "
                "ACL (default_action=Deny) oppure un Private Endpoint."
            )

    return " ".join(actions) if actions else "Nessuna azione derivabile dallo stato osservato."


def check(
    resources: list[str],
    networking: str = "vnet_only",
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Valuta la realizzabilità della postura di rete richiesta per un set di risorse.

    resources: elenco di tipi di risorsa (es. ["storage_account","data_factory","log_analytics"]).
    networking: postura desiderata. Valori supportati:
        - "vnet_only"           : accesso solo da VNet, internet bloccato (salvo ACL esplicite)
        - "all_in_one_subnet"   : richiesta letterale "tutto dentro la stessa subnet"
        - "private_endpoint"    : accesso via Private Endpoint
        - "public"              : accesso pubblico (nessuna restrizione)
    existing: (opz.) stato reale già osservato dai tool di lettura, per incrociare la
        knowledge base con la realtà. Mappa {tipo_risorsa: {fatti...}}, es.
        {"data_factory": {"managed_vnet_enabled": false},
         "storage_account": {"private_endpoint_exists": true}}.
        Quando presente, ogni risorsa riporta 'observed' + 'action_needed' concreto.

    Restituisce un'analisi per risorsa + un verdetto complessivo + raccomandazioni,
    così l'agente può proporre l'architettura *corretta* invece di tradurre alla cieca.
    """
    posture = (networking or "vnet_only").strip().lower()
    existing = existing or {}
    per_resource: list[dict[str, Any]] = []
    warnings: list[str] = []
    literal_achievable = True

    for rt in resources:
        key = _canon(rt)
        cap = _CAP.get(key)
        if not cap:
            per_resource.append({
                "resource": rt, "known": False,
                "note": "Tipo non in knowledge base: verificare manualmente il modello di rete.",
            })
            literal_achievable = False
            continue

        entry = {
            "resource": rt,
            "canonical": key,
            "known": True,
            "service_endpoint": cap["service_endpoint"],
            "private_endpoint": cap["private_endpoint"],
            "can_disable_public": cap["disable_public"],
            "vnet_injection": cap["vnet_injection"],
            "network_model": cap["model"],
        }

        if posture in ("all_in_one_subnet",):
            # "dentro la stessa subnet" è letterale solo per risorse con VNet injection.
            entry["achievable_literally"] = cap["vnet_injection"]
            if not cap["vnet_injection"]:
                literal_achievable = False
                if cap["service_endpoint"]:
                    entry["recommended"] = "Non entra nella subnet; usa service endpoint + network ACL (accesso ristretto ALLA subnet)."
                elif cap["private_endpoint"]:
                    entry["recommended"] = "Non entra nella subnet; usa Private Endpoint nella subnet (NIC privata) o modello dedicato."
                else:
                    entry["recommended"] = "Modello di rete dedicato (es. AMPLS/Managed VNet): non collocabile in una subnet condivisa."
        elif posture in ("vnet_only", "private_endpoint"):
            reachable = cap["service_endpoint"] or cap["private_endpoint"]
            entry["achievable"] = reachable and cap["disable_public"]
            if not entry["achievable"]:
                literal_achievable = False
            entry["recommended"] = cap["model"]
        else:  # public
            entry["achievable"] = True
            entry["recommended"] = "Accesso pubblico (sconsigliato per dati sensibili)."

        # Incrocio con lo stato reale osservato (se fornito): azione concreta per risorsa.
        obs = existing.get(key)
        if obs is None:
            obs = existing.get(rt)
        if obs is not None:
            entry["observed"] = obs
            entry["action_needed"] = _action_needed(key, posture, obs)

        per_resource.append(entry)

    # Warning trasversali sulle incoerenze intent ↔ implementazione più comuni.
    canon_set = {_canon(r) for r in resources}
    if posture in ("vnet_only", "all_in_one_subnet"):
        if "data_factory" in canon_set:
            warnings.append(
                "Data Factory: NON si mette in una subnet. Per il traffico privato serve "
                "Managed VNet + Managed Private Endpoint (o PE inbound). Il RG non c'entra con la rete."
            )
        if "log_analytics" in canon_set:
            warnings.append(
                "Log Analytics: non supporta service endpoint/subnet. Isolamento di rete solo via "
                "Azure Monitor Private Link Scope (AMPLS) + Private Endpoint."
            )
        if "sql_server" in canon_set:
            warnings.append(
                "SQL: 'public_network_access_enabled=true' + firewall deny-all NON equivale a "
                "public access disabilitato. Per bloccarlo davvero: public_network_access_enabled=false."
            )
        warnings.append(
            "network ACL con bypass='AzureServices' lascia passare i servizi Azure fidati: "
            "se l'intento è 'solo VNet, nessuna eccezione', valutare bypass='None'."
        )

    verdict = (
        "La richiesta è realizzabile alla lettera per tutte le risorse."
        if literal_achievable
        else "La richiesta NON è realizzabile alla lettera per ogni risorsa: applicare i modelli "
             "di rete corretti indicati per risorsa (vedi 'recommended') e confermare il delta con l'utente."
    )

    return {
        "networking": posture,
        "per_resource": per_resource,
        "warnings": warnings,
        "literally_achievable": literal_achievable,
        "verdict": verdict,
    }
