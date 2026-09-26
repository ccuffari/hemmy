"""Interfaccia a riga di comando dell'agente ADF.

Costruisce l'agente (config YAML + prompt + tool + guardrail + memoria) e
fornisce un ciclo interattivo domanda/risposta sul terminale.
"""

from __future__ import annotations

import argparse
import json
import os
from functools import partial
from pathlib import Path
from typing import Any
import sys

import yaml

def _resolve_project_root() -> Path:
    """Cartella che contiene `config/`, `data/`, `logs/`, `docs/`, `infra/`.

    1. `HEMMY_PROJECT_ROOT` (env var) — SEMPRE presente in un container
       (impostata dal Dockerfile a `/app`): OBBLIGATORIA lì perché
       `Path(__file__).resolve().parents[3]` NON può funzionare in un
       install non-editable (`pip install .`, quello usato in produzione).
       In quel caso il pacchetto finisce sotto
       `.../site-packages/hemmy/interfaces/cli.py`: risalendo di 3 livelli
       si arriva a `.../site-packages/../..` (es. `/usr/local/lib/python3.11`),
       una cartella COMPLETAMENTE ESTRANEA a dove sta `config/` — non è un
       problema di "conteggio sbagliato", è che le due gerarchie (codice
       installato vs. config/data copiati a fianco) sono semplicemente
       DISGIUNTE in un container: nessun `parents[N]` può ricongiungerle.
       Bug reale osservato in produzione: `resolve_llm_config` falliva per
       ogni utente con `FileNotFoundError: config/agent.yaml` risolto contro
       `/usr/local/lib/python3.11/config/agent.yaml`.
    2. Fallback: `parents[3]` da questo file (`src/hemmy/interfaces/cli.py`),
       valido SOLO con un install editable (`pip install -e .`, quello dello
       sviluppo locale), dove Python importa il pacchetto direttamente dalla
       sua posizione reale sotto `src/`.
    """
    env_root = os.environ.get("HEMMY_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _resolve_project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
AUDIT_PATH = str(PROJECT_ROOT / "logs" / "audit.log")
SESSION_PATH = PROJECT_ROOT / ".session.json"
DOCS_STATE_PATH = str(PROJECT_ROOT / "docs" / "infrastructure_state.json")
DOCS_MD_PATH = str(PROJECT_ROOT / "docs" / "INFRASTRUCTURE.md")
IAC_DIR = str(PROJECT_ROOT / "infra")
UPLOADS_DIR = str(PROJECT_ROOT / "uploads")

# Descrizioni dei tool mostrate al modello nel prompt di sistema.
TOOL_DOCS: dict[str, str] = {
    "adf.list_pipelines": "Elenca le pipeline della Data Factory. Args: {}",
    "adf.get_pipeline": "Definizione di una pipeline. Args: {\"pipeline_name\": str}",
    "adf.get_pipeline_runs": "Esecuzioni recenti. Args: {\"last_days\": int (opzionale)}",
    "adf.get_activity_runs": "Attività di una run. Args: {\"run_id\": str}",
    "adf.get_triggers": "Elenca i trigger e il loro stato. Args: {}",
    "adf.get_factory_identity": (
        "Managed Identity della Data Factory (principal_id per RBAC, es. per dare "
        "accesso al Key Vault). Args: {}"
    ),
    "adf.list_linked_services": "Elenca i Linked Service (nome+tipo). Args: {}",
    "adf.get_linked_service": (
        "Definizione di un Linked Service (tipo, config; segreti non esposti). "
        "Args: {\"name\": str}"
    ),
    "adf.list_datasets": "Elenca i Dataset (nome+tipo). Args: {}",
    "adf.get_dataset": (
        "Definizione di un Dataset, incl. il Linked Service referenziato. "
        "Args: {\"name\": str}"
    ),
    "blob.list_storage_accounts": (
        "Elenca gli storage account della subscription (management-plane). "
        "Args: {\"resource_group\": str (opz)}"
    ),
    "blob.list_containers": "Elenca i container dello storage configurato. Args: {}",
    "blob.list_containers_for_account": (
        "Elenca i container di un ALTRO storage account. La connection string viene "
        "chiesta all'operatore in modo sicuro (mai esposta a te). "
        "Args: {\"account_name\": str}"
    ),
    "blob.list_blobs_for_account": (
        "Elenca i blob di un container in un ALTRO storage account (connection string "
        "chiesta all'operatore). Args: {\"account_name\": str, \"container\": str, "
        "\"prefix\": str (opz)}"
    ),
    "blob.get_blob_schema_for_account": (
        "Schema di un file in un ALTRO storage account (connection string chiesta "
        "all'operatore). Args: {\"account_name\": str, \"container\": str, "
        "\"blob_name\": str}"
    ),
    "blob.list_blobs": "Elenca i blob. Args: {\"container\": str, \"prefix\": str (opz)}",
    "blob.get_blob_schema": "Schema di un file. Args: {\"container\": str, \"blob_name\": str}",
    "sql.list_tables": "Elenca le tabelle. Args: {\"schema\": str (opz, default dbo)}",
    "sql.get_table_schema": "Schema tabella. Args: {\"table\": str, \"schema\": str (opz)}",
    "sql.get_row_count": "Conteggio righe. Args: {\"table\": str, \"schema\": str (opz)}",
    "sql.list_tables_for_connection": (
        "Elenca tabelle di un ALTRO database. La connection string viene chiesta "
        "all'operatore in modo sicuro (mai esposta a te). "
        "Args: {\"resource_label\": str (opz), \"schema\": str (opz)}"
    ),
    "sql.get_table_schema_for_connection": (
        "Schema tabella di un ALTRO database (connection string chiesta all'operatore). "
        "Args: {\"table\": str, \"resource_label\": str (opz), \"schema\": str (opz)}"
    ),
    "sql.get_row_count_for_connection": (
        "Conteggio righe in un ALTRO database (connection string chiesta all'operatore). "
        "Args: {\"table\": str, \"resource_label\": str (opz), \"schema\": str (opz)}"
    ),
    "table.list_tables": "Elenca le tabelle di Table Storage. Args: {}",
    "queue.list_queues": "Elenca le code di Queue Storage. Args: {}",
    "fileshare.list_shares": "Elenca le file share. Args: {}",
    "fileshare.list_directories_and_files": (
        "Elenca directory/file in una share. "
        "Args: {\"share_name\": str, \"directory_path\": str (opz)}"
    ),
    # --- MANAGEMENT-PLANE (lettura) ---
    "docs.sync": (
        "Legge l'infrastruttura reale da Azure (ADF, storage e SQL di default, "
        "risorse del resource group) e rigenera la documentazione "
        "(docs/INFRASTRUCTURE.md con diagramma Mermaid). "
        "Args: {\"resource_group\": str (opz)}"
    ),
    "docs.add_note": (
        "Aggiunge/aggiorna una nota custom nella documentazione (persiste anche dopo "
        "docs.sync, upsert per titolo). Args: {\"title\": str, \"content\": str (markdown)}"
    ),
    "docs.remove_note": (
        "Rimuove una nota custom dalla documentazione. Args: {\"title\": str}"
    ),
    # --- Networking (lettura/diagnosi; provisioning via IaC) ---
    "network.list_vnets": (
        "Elenca le VNet (per resource group o subscription). "
        "Args: {\"resource_group\": str (opz)}"
    ),
    "network.list_subnets": (
        "Elenca le subnet di una VNet. Args: {\"resource_group\": str, \"vnet_name\": str}"
    ),
    "network.list_private_endpoints": (
        "Elenca i Private Endpoint. Args: {\"resource_group\": str (opz)}"
    ),
    "network.list_nsgs": "Elenca gli NSG. Args: {\"resource_group\": str (opz)}",
    "adf.list_managed_private_endpoints": (
        "Elenca i Managed Private Endpoint della Managed VNet di ADF. "
        "Args: {\"managed_vnet_name\": str (opz, default 'default')}"
    ),
    "adf.create_managed_vnet": (
        "[WRITE] Abilita la Managed VNet sulla Data Factory. "
        "Args: {\"managed_vnet_name\": str (opz)}"
    ),
    "adf.create_managed_private_endpoint": (
        "[WRITE] Crea un Managed Private Endpoint ADF verso una risorsa (va poi "
        "approvato lato risorsa target). Args: {\"name\": str, \"group_id\": str, "
        "\"private_link_resource_id\": str, \"managed_vnet_name\": str (opz)}"
    ),
    # --- RBAC ---
    "rbac.list_role_assignments": (
        "Elenca i role assignment a uno scope (RG/risorsa/subscription). "
        "Args: {\"scope\": str}"
    ),
    "rbac.assign_role": (
        "[WRITE] Assegna un ruolo a un principal. Richiede che il SP chiamante sia "
        "Owner/User Access Administrator. Args: {\"scope\": str, \"principal_id\": str, "
        "\"role_name\": str, \"principal_type\": str (opz, default ServicePrincipal)}"
    ),
    "rbac.remove_role_assignment": (
        "[WRITE] Rimuove i role assignment di un principal per un ruolo a uno scope. "
        "Args: {\"scope\": str, \"principal_id\": str, \"role_name\": str}"
    ),
    # --- Key Vault (segreti centralizzati) ---
    "keyvault.list_vaults": "Elenca i Key Vault della subscription. Args: {}",
    "keyvault.list_secrets": (
        "Elenca i NOMI dei segreti in un Key Vault (mai i valori). "
        "Args: {\"vault\": str}"
    ),
    "keyvault.set_secret": (
        "[WRITE] Salva un segreto in Key Vault. Il valore lo inserisce l'operatore in "
        "modo sicuro (mai esposto a te). Args: {\"vault\": str, \"secret_name\": str}"
    ),
    "keyvault.delete_secret": (
        "[WRITE] Elimina un segreto dal Key Vault. Args: {\"vault\": str, \"secret_name\": str}"
    ),
    "adf.create_keyvault_linked_service": (
        "[WRITE] Crea un Linked Service AzureKeyVault (ADF accede via Managed Identity). "
        "Args: {\"name\": str, \"vault_url\": str}"
    ),
    "adf.create_linked_service_kv": (
        "[WRITE] Crea un Linked Service Blob/SQL con connection string da Key Vault "
        "(riferimento, nessun segreto). Args: {\"name\": str, \"service_type\": "
        "\"AzureBlobStorage\"|\"AzureSqlDatabase\", \"kv_linked_service\": str, "
        "\"secret_name\": str}"
    ),
    # --- IaC / Terraform (provisioning dichiarativo) ---
    "iac.init": "Inizializza Terraform (provider/backend). Args: {}",
    "iac.validate": "Valida la configurazione Terraform. Args: {}",
    "iac.plan": (
        "Genera il piano Terraform (salvato) e ne riporta il riepilogo + i conteggi "
        "(add/change/destroy). Se destroy_count>0 NON applicare senza verifica: spesso è "
        "un refactor di indirizzo da gestire con iac.generate_moved. Args: {}"
    ),
    "iac.show": "Mostra stato/piano Terraform. Args: {}",
    "iac.state_list": "Elenca le risorse nello stato Terraform. Args: {}",
    "iac.output": "Restituisce gli output Terraform (JSON). Args: {}",
    "iac.write_file": (
        "Scrive/aggiorna un file .tf/.tfvars nella working dir (inerte finché non "
        "esegui apply). Args: {\"filename\": str, \"content\": str}"
    ),
    "iac.lint_files": (
        "Lint statico dei file Terraform PRIMA del commit (nessun binario): rileva "
        "placeholder/alias residui, graffe sbilanciate, count known-after-apply e delta "
        "intent/implementazione di rete. Chiamalo prima di github.commit_files: se "
        "ok=False non committare. Args: {\"files\": {path: content}}"
    ),
    "iac.apply": (
        "[WRITE] Applica il piano Terraform salvato ('tfplan'). Esegui prima iac.plan e "
        "fai revisionare l'output. Guardia: se il piano distrugge risorse l'apply è "
        "BLOCCATO salvo confirm_destroy=true (un destroy non atteso è spesso un refactor "
        "di indirizzo → usa iac.generate_moved). Args: {\"confirm_destroy\": bool (opz)}"
    ),
    "iac.generate_moved": (
        "Genera blocchi `moved {}` per un refactor di indirizzo Terraform (rinomina nello "
        "state senza destroy+create): l'alternativa CORRETTA a state rm/import in pipeline. "
        "Args: {\"moves\": [{\"from\": str, \"to\": str}], \"filename\": str (opz)}"
    ),
    "iac.destroy": "[WRITE] Distrugge le risorse gestite da Terraform. Args: {}",
    "iac.import": (
        "[WRITE] Importa una risorsa Azure esistente nello state Terraform "
        "(riconciliazione; il blocco .tf deve già esistere). "
        "Args: {\"address\": str, \"resource_id\": str}"
    ),
    "iac.configure_remote_backend": (
        "[WRITE] Configura il backend remoto azurerm (state su Storage) e migra lo "
        "state. Prereq: storage account + container esistenti, SP con Storage Blob "
        "Data Contributor. Args: {\"resource_group\": str, \"storage_account\": str, "
        "\"container\": str, \"key\": str (opz)}"
    ),
    "iac.scaffold": (
        "Genera l'HCL Terraform di una risorsa base (da salvare poi con iac.write_file). "
        "Args: {\"kind\": \"resource_group\"|\"storage_account\"|\"key_vault\"|"
        "\"data_factory\"|\"virtual_network\"|\"subnet\"|\"sql_server\"|\"sql_database\", "
        "\"name\": str, \"params\": {} (opz)}"
    ),
    "iac.scaffold_module": (
        "Genera un MODULO Terraform (modules/<name>/ con main+variables+outputs) e il "
        "'module_call' da inserire nell'ambiente. kind: resource_group | storage_account "
        "| key_vault | data_factory | sql_server | sql_database | log_analytics | "
        "diagnostic_setting | action_group | metric_alert | budget (observability/cost). "
        "Args: {\"kind\": str, \"name\": str, \"params\": {} (opz)}"
    ),
    "iac.scaffold_environment": (
        "Genera lo scheletro di un AMBIENTE (environments/<env>/ con providers, backend, "
        "locals, variables, main, tfvars). Args: {\"env\": \"dev\"|\"uat\"|\"prod\", "
        "\"storage_account\": str (opz), \"container\": str (opz), \"resource_group\": "
        "str (opz), \"location\": str (opz)}"
    ),
    # --- CI/CD (provider configurabile: github | azure_devops) ---
    "cicd.generate_pipeline": (
        "Genera i file pipeline CI/CD per il provider configurato. Senza 'environment' "
        "genera TUTTI gli ambienti (dev/uat/prod); con 'environment' solo quello. "
        "Args: {\"environment\"?: \"dev\"|\"uat\"|\"prod\"}"
    ),
    "cicd.trigger_pipeline": (
        "[WRITE] Avvia la pipeline DEDICATA a un ambiente con un comando Terraform. "
        "dev = deploy di base; uat/prod solo su richiesta esplicita dell'utente. "
        "Args: {\"command\": \"init\"|\"validate\"|\"plan\"|\"apply\"|\"destroy\", "
        "\"environment\"?: \"dev\"|\"uat\"|\"prod\" (default dev)}"
    ),
    "cicd.get_status": (
        "Esecuzioni recenti della pipeline CI/CD. Args: {\"limit\": int (opz)}"
    ),
    "cicd.wait_for_run": (
        "Attende il completamento di una run CI/CD e ne riporta l'esito. "
        "Args: {\"run_id\": str, \"timeout_seconds\": int (opz), \"poll_interval\": int (opz)}"
    ),
    "cicd.get_run_jobs": (
        "Job e step di una run (identifica lo step fallito). Args: {\"run_id\": str}"
    ),
    "cicd.get_run_logs": (
        "Scarica i log dei job FALLITI di una run per diagnosticare la causa. "
        "Args: {\"run_id\": str}"
    ),
    "cicd.render_pipeline": (
        "Restituisce path+contenuto della pipeline CI/CD di UN ambiente senza scrivere "
        "su disco (per committarla direttamente sul repo). "
        "Args: {\"environment\"?: \"dev\"|\"uat\"|\"prod\" (default dev)}"
    ),
    "cicd.render_pipelines": (
        "Restituisce {path: contenuto} delle pipeline di TUTTI gli ambienti "
        "(dev/uat/prod) per committarle in un unico commit. Rispetta cicd.yaml 'auth' "
        "(secret|oidc): con oidc le pipeline non usano ARM_CLIENT_SECRET. Args: {}"
    ),
    "cicd.generate_oidc_federation": (
        "Genera la config di Workload Identity Federation (OIDC) GitHub→Azure: per ogni "
        "subject (ambienti + branch) la federated credential e il comando "
        "`az ad app federated-credential create` da eseguire una-tantum. OIDC elimina "
        "ARM_CLIENT_SECRET dalla pipeline. Args: {\"app_id\"?: str, \"environments\"?: "
        "[str], \"branch\"?: str, \"repo\"?: str}"
    ),
    "naming.build": (
        "Genera un nome risorsa conforme alla naming convention Azure "
        "(config/naming.yaml): pattern <abbr>-<workload>-<env>-<region>-<instance> "
        "(storage account senza trattini). USA questo per nominare le risorse invece "
        "di inventare nomi. Args: {\"resource_type\": str, \"workload\": str, "
        "\"environment\": \"dev\"|\"uat\"|\"prod\", \"region\"?: str, \"instance\"?: str}"
    ),
    "feasibility.check": (
        "Verifica se l'architettura di rete richiesta è tecnicamente coerente col "
        "modello Azure, PRIMA di generare Terraform. Restituisce, per tipo di risorsa, "
        "il modello di rete corretto (service endpoint / private endpoint / VNet injection / "
        "modello dedicato), i warning intent↔implementazione e un verdetto di realizzabilità. "
        "USA questo prima di tradurre richieste tipo 'metti tutto in una subnet'. "
        "Passa 'existing' con lo stato reale letto dai tool (es. {\"data_factory\": "
        "{\"managed_vnet_enabled\": false}}) per ottenere 'action_needed' concreto per "
        "risorsa. Args: {\"resources\": [str], \"networking\"?: \"vnet_only\"|"
        "\"all_in_one_subnet\"|\"private_endpoint\"|\"public\", \"existing\"?: {type: {facts}}}"
    ),
    "remediation.classify": (
        "Classifica il RISCHIO di una remediation (low/medium/high) e indica come "
        "procedere: low → proponi e procedi con l'approvazione standard; high → richiedi "
        "conferma esplicita dell'impatto e preferisci l'alternativa più sicura. USA questo "
        "dopo aver diagnosticato un errore, prima di applicare la fix. remediation es.: "
        "moved_block, for_each_refactor, import_resource, state_rm, apply_with_destroy, "
        "rbac_assign, move_resources, force_unlock. "
        "Args: {\"remediation\": str, \"error\"?: str}"
    ),
    # --- File allegati (primitiva upload UI: 📎) ---
    "files.list_uploads": (
        "Elenca i file allegati dall'utente (nome, path assoluto, dimensione, estensione). "
        "USA per scoprire i file caricati via 📎 e passarne il path ai tool. Args: {}"
    ),
    "files.read_upload_text": (
        "Legge il contenuto testuale di un file allegato (es. .drawio/.xml/.json/.csv/.tf). "
        "Per file binari (.pptx, immagini) serve un tool dedicato. "
        "Args: {\"name\": str, \"max_bytes\"?: int}"
    ),
    # --- Meta-tooling: l'agente estende/modifica sé stesso (con approvazione) ---
    "meta.list_capabilities": (
        "Inventario dei tool attuali raggruppati per dominio + plugin installati. "
        "USA per ragionare sulla copertura prima di proporre un nuovo tool. Args: {}"
    ),
    "meta.analyze_request": (
        "Valuta se un task è coperto da un tool esistente, da una COMBINAZIONE di tool, "
        "o se serve un nuovo tool. USA come PRIMO passo quando la richiesta sembra non "
        "avere un tool dedicato. Args: {\"description\": str, \"keywords\"?: [str]}"
    ),
    "meta.propose_tool": (
        "Valida (senza scrivere nulla) un NUOVO tool generato dall'AI: nome 'domain.action', "
        "codice Python con `def run(**kwargs)`, flag write, eventuale modifica UI. Ritorna "
        "esito sintetico (valid/errors/warnings). USA prima di meta.install_tool. "
        "Args: {\"name\": str, \"doc\": str, \"code\": str, \"write\"?: bool, \"ui\"?: str}"
    ),
    "meta.install_tool": (
        "[WRITE] Installa ufficialmente il tool tra i sorgenti (cartella plugins) dopo "
        "validazione; il catalogo tool viene poi ricaricato a caldo in automatico, senza "
        "alcun riavvio del sistema. Chiedi sempre approvazione e mostra "
        "la preview del codice. Args: {\"name\": str, \"doc\": str, \"code\": str, \"write\"?: bool, \"ui\"?: str}"
    ),
    "meta.list_plugins": "Elenca i plugin (tool generati a runtime) installati e il loro stato. Args: {}",
    "meta.remove_plugin": "[WRITE] Rimuove un plugin per nome tool; il catalogo tool viene ricaricato a caldo in automatico, senza riavvio. Args: {\"name\": str}",
    # --- GitHub (lettura remota: niente working copy locale) ---
    "github.get_default_branch": "Branch di default del repo GitHub configurato. Args: {}",
    "github.get_file": (
        "Legge un file dal repo GitHub e ne restituisce il contenuto testuale. "
        "USA questo per leggere il codice esistente PRIMA di modificarlo con "
        "github.commit_files (evita di sovrascrivere alla cieca). "
        "Args: {\"path\": str, \"ref\"?: str}"
    ),
    "github.list_directory": (
        "Elenca il contenuto di una directory del repo (nome, tipo, dimensione). "
        "path vuoto = root. Args: {\"path\"?: str, \"ref\"?: str}"
    ),
    "github.get_tree": (
        "Albero completo ricorsivo dei file del repo (vista d'insieme). "
        "Args: {\"ref\"?: str}"
    ),
    "github.read_folder": (
        "Legge una CARTELLA del repo GitHub e restituisce TUTTI i file con il loro "
        "contenuto in un'unica chiamata (evita N chiamate a github.get_file). "
        "Ricorsiva di default, con filtri per estensione e limiti anti-saturazione. "
        "USA questo per ispezionare moduli Terraform, pipeline CI/CD o cartelle intere "
        "senza clonare il repo in locale. "
        "Args: {\"path\": str, \"recursive\": bool (opz, default true), "
        "\"extensions\": [str] (opz, es. [\".tf\", \".yaml\"]), "
        "\"max_files\": int (opz, default 50), \"max_bytes_per_file\": int (opz, "
        "default 200000), \"ref\"?: str}"
    ),
    "github.get_files": (
        "Legge PIÙ file del repo GitHub in un'unica chiamata batch (paths espliciti). "
        "Args: {\"paths\": [str], \"ref\"?: str, \"max_bytes_per_file\": int (opz)}"
    ),
    "github.search_code": (
        "Cerca codice nel repo GitHub con query testuale o regex-like (code search "
        "semplice lato client sull'albero del repo). Restituisce path + righe match. "
        "Args: {\"query\": str, \"path\"?: str (sottocartella di partenza), "
        "\"extensions\"?: [str], \"max_results\"?: int (opz, default 20), \"ref\"?: str}"
    ),
    "github.get_latest_run": (
        "Ultima esecuzione della CI, opz. filtrata per file workflow. "
        "Comodo per collegare diagnosi → cicd.get_run_logs. "
        "Args: {\"workflow\"?: str}"
    ),
    "github.get_commit": (
        "Dettaglio di un commit: messaggio, autore, data e file toccati (con stato). "
        "Args: {\"sha\"?: str (default HEAD)}"
    ),
    "github.get_diff": (
        "Confronto tra due ref (base...head): file modificati con patch. Utile per "
        "rivedere cosa cambia prima di un apply. Args: {\"base\": str, \"head\": str}"
    ),
    "github.get_workflow": (
        "Metadati dei workflow GitHub Actions (nome, path, stato); per il contenuto YAML "
        "usa github.get_file. Args: {\"workflow\"?: str (file o id; omesso = tutti)}"
    ),
    "github.set_secret": (
        "[WRITE] Crea/aggiorna un GitHub Actions secret nel repo (es. ARM_* per la CI). "
        "Il valore lo inserisce l'operatore in modo sicuro (cifrato, mai esposto a te). "
        "Args: {\"name\": str}"
    ),
    "github.commit_files": (
        "[WRITE] Committa più file DIRETTAMENTE sul repo GitHub in un unico commit "
        "(niente working copy locale). Se il branch non esiste su un repo popolato, lo "
        "CREA dal branch di default e committa lì. Args: {\"files\": {\"path\": "
        "\"contenuto\"}, \"message\": str, \"branch\": str (opz, default main), "
        "\"create_branch_if_missing\": bool (opz, default true)}"
    ),
    "github.list_branches": (
        "Elenca i branch del repo (nome, protetto, sha) e il branch di default. Args: {}"
    ),
    "github.create_branch": (
        "[WRITE] Crea un nuovo branch dal branch di default (o da 'from_ref': "
        "branch/tag/sha). Idempotente. Args: {\"branch\": str, \"from_ref\": str (opz)}"
    ),
    "github.delete_branch": (
        "[WRITE] Elimina un branch (cleanup dopo il merge). Mai il branch di default. "
        "Idempotente. Args: {\"branch\": str}"
    ),
    "github.create_pull_request": (
        "[WRITE] Apre una Pull Request head->base (promozione GitFlow: feature->dev, "
        "dev->uat, uat->prod). Args: {\"head\": str, \"base\": str, \"title\": str, "
        "\"body\": str (opz), \"draft\": bool (opz)}"
    ),
    "github.merge_pull_request": (
        "[WRITE] Mergia una PR approvata (promozione tra ambienti). Fallisce se non "
        "mergiabile. Args: {\"number\": int, \"method\": \"merge\"|\"squash\"|\"rebase\" (opz)}"
    ),
    "github.list_pull_requests": (
        "Elenca le Pull Request del repo. Args: {\"state\": \"open\"|\"closed\"|\"all\" "
        "(opz), \"base\": str (opz)}"
    ),
    "github.get_pull_request": (
        "Dettaglio di una PR: stato, mergiabilità, branch, conteggi file. "
        "Args: {\"number\": int}"
    ),
    "lock.list_locks": (
        "Elenca i Management Lock (per resource group o subscription). "
        "Args: {\"resource_group\": str (opz)}"
    ),
    "lock.create_lock": (
        "[WRITE] Crea un Management Lock su un resource group (protezione). "
        "Args: {\"resource_group\": str, \"lock_name\": str, \"level\": "
        "\"CanNotDelete\"|\"ReadOnly\", \"notes\": str (opz)}"
    ),
    "lock.delete_lock": (
        "[WRITE] Rimuove un Management Lock da un resource group. "
        "Args: {\"resource_group\": str, \"lock_name\": str}"
    ),
    "resource.list_resource_groups": "Elenca i resource group. Args: {}",
    "resource.list_resources": (
        "Elenca le risorse di un resource group (nome, tipo, id). "
        "Args: {\"resource_group\": str}"
    ),
    "resource.move_resources": (
        "[WRITE] Sposta risorse tra resource group (ARM Resource Move). Operazione "
        "delicata (PE/DNS/Managed VNet possono rompersi). Args: {\"source_resource_group\": "
        "str, \"resource_ids\": [str], \"target_resource_group\": str}"
    ),
    "sql.list_sql_servers": "Elenca i SQL Server della subscription. Args: {}",
    "sql.list_sql_databases": (
        "Elenca i database di un SQL Server. "
        "Args: {\"resource_group\": str, \"server_name\": str}"
    ),
    # --- SCRITTURA (richiedono approvazione umana) ---
    "adf.create_linked_service": (
        "[WRITE] Crea/aggiorna un Linked Service (connessione Blob/SQL). La connection "
        "string viene chiesta all'operatore in modo sicuro (mai esposta a te). "
        "Args: {\"name\": str, \"service_type\": \"AzureBlobStorage\"|\"AzureSqlDatabase\"}"
    ),
    "adf.create_dataset": (
        "[WRITE] Crea/aggiorna un Dataset. "
        "Args: {\"name\": str, \"definition\": {DatasetResource JSON}}"
    ),
    "adf.create_pipeline": (
        "[WRITE] Crea/aggiorna una pipeline. "
        "Args: {\"pipeline_name\": str, \"definition\": {PipelineResource JSON}}"
    ),
    "adf.trigger_pipeline_run": (
        "[WRITE] Avvia una pipeline. "
        "Args: {\"pipeline_name\": str, \"parameters\": {} (opz)}"
    ),
    "adf.run_and_wait": (
        "[WRITE] Avvia una pipeline e ATTENDE l'esito (con errori attività). "
        "Args: {\"pipeline_name\": str, \"parameters\": {} (opz), "
        "\"timeout_seconds\": int (opz), \"poll_interval\": int (opz)}"
    ),
    "adf.create_copy_pipeline": (
        "[WRITE] Crea in un colpo solo dataset sorgente+destinazione e pipeline di "
        "copia Blob(file)->SQL con mapping/cast colonne. I Linked Service devono già "
        "esistere. Args: {\"pipeline_name\": str, \"source_linked_service\": str, "
        "\"source_container\": str, \"source_file\": str, \"sink_linked_service\": str, "
        "\"sink_table\": str, \"column_mappings\": [{\"source\": str, \"sink\": str, "
        "\"type\": str (opz)}], \"source_format\": \"DelimitedText\"|\"Excel\"|\"Json\""
        "|\"Parquet\" (opz), \"source_sheet\": str (opz, Excel), \"sink_schema\": str "
        "(opz, default dbo)}"
    ),
    "adf.delete_linked_service": "[WRITE] Elimina un Linked Service. Args: {\"name\": str}",
    "adf.delete_dataset": "[WRITE] Elimina un Dataset. Args: {\"name\": str}",
    "adf.delete_pipeline": "[WRITE] Elimina una pipeline. Args: {\"pipeline_name\": str}",
    "sql.execute_write": "[WRITE] Esegue SQL DDL/DML sul DB configurato. Args: {\"query\": str}",
    "sql.execute_write_for_connection": (
        "[WRITE] Esegue SQL DDL/DML su un ALTRO database. Doppio gate: connection "
        "string chiesta all'operatore + approvazione. "
        "Args: {\"query\": str, \"resource_label\": str (opz)}"
    ),
    "blob.upload": (
        "[WRITE] Carica un blob. "
        "Args: {\"container\": str, \"blob_name\": str, \"content\": str}"
    ),
    "blob.create_container": "[WRITE] Crea un container. Args: {\"container\": str}",
    "blob.delete_container": "[WRITE] Elimina un container. Args: {\"container\": str}",
    "table.create_table": "[WRITE] Crea una tabella (Table Storage). Args: {\"table_name\": str}",
    "table.delete_table": "[WRITE] Elimina una tabella (Table Storage). Args: {\"table_name\": str}",
    "queue.create_queue": "[WRITE] Crea una coda (Queue Storage). Args: {\"queue_name\": str}",
    "queue.delete_queue": "[WRITE] Elimina una coda (Queue Storage). Args: {\"queue_name\": str}",
    "fileshare.create_share": "[WRITE] Crea una file share. Args: {\"share_name\": str}",
    "fileshare.create_directory": (
        "[WRITE] Crea una directory in una share. "
        "Args: {\"share_name\": str, \"directory_path\": str}"
    ),
    "fileshare.delete_share": "[WRITE] Elimina una file share. Args: {\"share_name\": str}",
    # --- MANAGEMENT-PLANE (creazione risorse di primo livello) ---
    "resource.create_resource_group": (
        "[WRITE] Crea un resource group. Args: {\"name\": str, \"location\": str}"
    ),
    "blob.create_storage_account": (
        "[WRITE] Crea uno storage account. Args: {\"resource_group\": str, "
        "\"account_name\": str, \"location\": str, \"sku\": str (opz), \"kind\": str (opz)}"
    ),
    "sql.create_sql_server": (
        "[WRITE] Crea un SQL Server. Login e password admin chiesti all'operatore "
        "(mai esposti a te). Args: {\"resource_group\": str, \"server_name\": str, "
        "\"location\": str}"
    ),
    "sql.create_sql_database": (
        "[WRITE] Crea un database su un SQL Server. Args: {\"resource_group\": str, "
        "\"server_name\": str, \"database_name\": str, \"location\": str}"
    ),
}


def _load_config() -> dict[str, Any]:
    """Carica config/agent.yaml."""
    with open(CONFIG_DIR / "agent.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_guardrails() -> dict[str, Any]:
    """Carica config/guardrails.yaml.

    Ritorna sempre un dict. Se il file manca o è vuoto, ritorna un dizionario
    vuoto invece di None: la sua assenza è un errore di configurazione
    dell'operatore, non deve far crashare l'inizializzazione dell'agente.
    """
    path = CONFIG_DIR / "guardrails.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _load_cicd() -> dict[str, Any]:
    """Carica config/cicd.yaml (provider CI/CD e impostazioni)."""
    path = CONFIG_DIR / "cicd.yaml"
    if not path.exists():
        return {"provider": "github", "iac_dir": "infra"}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_naming() -> dict[str, Any]:
    """Carica config/naming.yaml (naming convention delle risorse)."""
    path = CONFIG_DIR / "naming.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_prompts() -> dict[str, str]:
    """Carica config/prompts.md e lo suddivide nelle sezioni system/planner/guardrail."""
    text = (CONFIG_DIR / "prompts.md").read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    mapping = {
        "system prompt": "system",
        "planner prompt": "planner",
        "guardrail prompt": "guardrail",
    }

    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buffer).strip()
            title = line[3:].strip().lower()
            current = mapping.get(title)
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer).strip()

    return sections


def _user_workspace(data_subdir: str, legacy_dir: Path, user_id: Any) -> Path:
    """Cartella di lavoro isolata per utente (docs/uploads/iac).

    SICUREZZA: prima di questo fix tutti gli utenti web condividevano LE STESSE
    cartelle — incluso `infra/`, che è il workspace Terraform del progetto
    stesso: un utente poteva sovrascrivere lo stato infrastrutturale
    documentato di un altro, o far girare `iac.plan`/`iac.apply`/`iac.destroy`
    sullo stesso stato Terraform di un altro utente. Con `user_id` valorizzato
    (sempre vero per richieste web autenticate) ogni utente ottiene una
    cartella dedicata sotto `data/<subdir>/users/<id>/`, fuori dal codice
    sorgente (stesso principio già usato per `plugins/`, vedi `plugins/__init__.py`).
    Se `user_id` è None (solo CLI locale, singolo operatore, nessun portale) si
    usa la cartella storica condivisa: comportamento invariato per quel caso
    d'uso a singolo utente.
    """
    if user_id is None:
        return legacy_dir
    root = PROJECT_ROOT / "data" / data_subdir / "users" / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_uploads_dir(user_id: Any) -> str:
    """Cartella upload isolata per utente.

    Usata anche da `interfaces/web.py` per l'endpoint `/api/upload`: DEVE
    restare in sync con quella legata ai tool `files.*` in `_build_agent`,
    altrimenti l'agente non vedrebbe (o vedrebbe quelli sbagliati) i file
    caricati dall'utente.
    """
    return str(_user_workspace("uploads", PROJECT_ROOT / "uploads", user_id))


def _build_agent(
    approval_fn: Any = None,
    secret_prompt_fn: Any = None,
    user_id: Any = None,
    llm_config: dict[str, Any] | None = None,
) -> Any:
    """Assembla l'agente con tutte le dipendenze (client, tool, guardrail, memoria).

    approval_fn / secret_prompt_fn: callback opzionali per approvazione e input
    segreti. Se None si usano i prompt CLI (input/getpass); la UI web inietta
    callback basati su eventi/queue. In entrambi i casi il codice di agente/tool/
    guardrail è identico.

    user_id: se valorizzato (app multi-utente), i tool generati a runtime vengono
    caricati/installati nell'area ISOLATA del singolo utente (`plugins/users/<id>/`),
    non nella cartella condivisa. La baseline nativa resta comune a tutti.

    llm_config (BYOK): {"provider", "model", "base_url", "api_key"} calcolato dal
    chiamante a partire dalle Impostazioni dell'utente (`interfaces/web.py`). Se
    None, l'agente ripiega sulle variabili d'ambiente `DEEPSEEK_*` — SOLO per la
    CLI locale senza portale (nessun utente autenticato).
    """
    from hemmy.core.agent import ADFAgent
    from hemmy.guardrails.guardrails import Guardrails
    from hemmy.infra import clients
    from hemmy.memory.memory import ShortTermMemory
    from hemmy.audit.audit import AuditLog
    from hemmy.docs.docgen import DocStore
    from hemmy.tools.adf import adf_tools
    from hemmy.tools.blob import blob_tools
    from hemmy.tools.cicd import cicd_tools
    from hemmy.tools.docs import docs_tools
    from hemmy.tools.feasibility import feasibility_tools
    from hemmy.tools.github import github_tools
    from hemmy.tools.iac import iac_tools
    from hemmy.tools.remediation import remediation_tools
    from hemmy.tools.meta import meta_tools
    from hemmy.tools.files import files_tools
    from hemmy.tools.keyvault import keyvault_tools
    from hemmy.tools.lock import lock_tools
    from hemmy.tools.naming import naming_tools
    from hemmy.tools.network import network_tools
    from hemmy.tools.rbac import rbac_tools
    from hemmy.tools.fileshare import fileshare_tools
    from hemmy.tools.queue import queue_tools
    from hemmy.tools.resource import resource_tools
    from hemmy.tools.sql import sql_tools
    from hemmy.tools.table import table_tools
    from hemmy.utils.helpers import SessionSecretProvider

    # Provider con cache di sessione: il segreto per una risorsa viene chiesto una
    # sola volta e riusato (resta in-process, mai passato al modello LLM). Il prompt
    # è iniettabile (CLI getpass di default, oppure callback web basato su eventi).
    prompt_secret = SessionSecretProvider(prompt_fn=secret_prompt_fn)

    config = _load_config()
    prompts = _load_prompts()
    policy = _load_guardrails()
    cicd_config = _load_cicd()
    naming_config = _load_naming()

    rg = "rgtfstatedevwe01"
    factory = "adftfstatedevwe01"

    # Client LAZY: nessuna connessione all'avvio. Ogni servizio viene contattato solo
    # quando un tool lo usa (e poi riusato). Startup istantaneo e resiliente: se un
    # servizio è irraggiungibile, fallisce solo il tool che lo usa, non l'intera app.
    from hemmy.infra.clients import LazyClient

    adf_client = LazyClient(clients.get_adf_client)
    blob_client = LazyClient(clients.get_blob_client)
    table_service = LazyClient(clients.get_table_client)
    queue_service = LazyClient(clients.get_queue_client)
    share_service = LazyClient(clients.get_share_client)
    storage_mgmt_client = LazyClient(clients.get_storage_mgmt_client)
    sql_mgmt_client = LazyClient(clients.get_sql_mgmt_client)
    resource_mgmt_client = LazyClient(clients.get_resource_mgmt_client)
    keyvault_mgmt_client = LazyClient(clients.get_keyvault_mgmt_client)
    authorization_client = LazyClient(clients.get_authorization_client)
    network_client = LazyClient(clients.get_network_client)
    lock_client = LazyClient(clients.get_lock_client)
    credential = LazyClient(clients.get_credential)
    sql_conn = LazyClient(clients.get_sql_connection)

    # Workspace isolati per utente (docs/uploads/iac) — vedi `_user_workspace`.
    docs_dir = _user_workspace("docs", PROJECT_ROOT / "docs", user_id)
    iac_dir = str(_user_workspace("iac", PROJECT_ROOT / "infra", user_id))
    uploads_dir = user_uploads_dir(user_id)

    # Copia locale (per-agente) del catalogo doc statico: i tool RUNTIME
    # installati da un utente NON devono inquinare il dizionario globale di
    # processo `TOOL_DOCS` (altrimenti nome+descrizione dei suoi plugin privati
    # finirebbero visibili anche nell'agente/catalogo `/api/tools` di un altro
    # utente — vedi il merge più sotto).
    tool_docs = dict(TOOL_DOCS)

    docs = DocStore(
        str(docs_dir / "infrastructure_state.json"),
        str(docs_dir / "INFRASTRUCTURE.md"),
        factory_name=factory,
    )

    # I client/parametri fissi vengono legati; il modello passa solo gli args specifici.
    tools = {
        "adf.list_pipelines": partial(adf_tools.list_pipelines, adf_client, rg, factory),
        "adf.get_pipeline": partial(adf_tools.get_pipeline, adf_client, rg, factory),
        "adf.get_pipeline_runs": partial(adf_tools.get_pipeline_runs, adf_client, rg, factory),
        "adf.get_activity_runs": partial(adf_tools.get_activity_runs, adf_client, rg, factory),
        "adf.get_triggers": partial(adf_tools.get_triggers, adf_client, rg, factory),
        "adf.get_factory_identity": partial(
            adf_tools.get_factory_identity, adf_client, rg, factory
        ),
        "adf.list_managed_private_endpoints": partial(
            adf_tools.list_managed_private_endpoints, adf_client, rg, factory
        ),
        # Networking (lettura/diagnosi)
        "network.list_vnets": partial(network_tools.list_virtual_networks, network_client),
        "network.list_subnets": partial(network_tools.list_subnets, network_client),
        "network.list_private_endpoints": partial(
            network_tools.list_private_endpoints, network_client
        ),
        "network.list_nsgs": partial(
            network_tools.list_network_security_groups, network_client
        ),
        "adf.list_linked_services": partial(
            adf_tools.list_linked_services, adf_client, rg, factory
        ),
        "adf.get_linked_service": partial(
            adf_tools.get_linked_service, adf_client, rg, factory
        ),
        "adf.list_datasets": partial(adf_tools.list_datasets, adf_client, rg, factory),
        "adf.get_dataset": partial(adf_tools.get_dataset, adf_client, rg, factory),
        "blob.list_storage_accounts": partial(
            blob_tools.list_storage_accounts, storage_mgmt_client
        ),
        "blob.list_containers": partial(blob_tools.list_containers, blob_client),
        # Accesso a storage account arbitrari: connection string chiesta all'operatore
        # (man-in-the-middle) e mai passata al modello LLM.
        "blob.list_containers_for_account": partial(
            blob_tools.list_containers_for_account, prompt_secret
        ),
        "blob.list_blobs_for_account": partial(
            blob_tools.list_blobs_for_account, prompt_secret
        ),
        "blob.get_blob_schema_for_account": partial(
            blob_tools.get_blob_schema_for_account, prompt_secret
        ),
        "blob.list_blobs": partial(blob_tools.list_blobs, blob_client),
        "blob.get_blob_schema": partial(blob_tools.get_blob_schema, blob_client),
        "table.list_tables": partial(table_tools.list_tables, table_service),
        "queue.list_queues": partial(queue_tools.list_queues, queue_service),
        "fileshare.list_shares": partial(fileshare_tools.list_shares, share_service),
        "fileshare.list_directories_and_files": partial(
            fileshare_tools.list_directories_and_files, share_service
        ),
        # Documentazione: sincronizza lo stato reale da Azure e rigenera i doc.
        "docs.sync": partial(
            docs_tools.sync,
            docs,
            adf_client,
            rg,
            factory,
            blob_client,
            table_service,
            queue_service,
            share_service,
            sql_conn,
            storage_mgmt_client,
            sql_mgmt_client,
            resource_mgmt_client,
            network_client,
        ),
        "docs.add_note": partial(docs_tools.add_note, docs),
        "docs.remove_note": partial(docs_tools.remove_note, docs),
        # RBAC (lettura)
        "rbac.list_role_assignments": partial(
            rbac_tools.list_role_assignments, authorization_client
        ),
        # Key Vault (lettura)
        "keyvault.list_vaults": partial(keyvault_tools.list_vaults, keyvault_mgmt_client),
        "keyvault.list_secrets": partial(keyvault_tools.list_secrets, credential),
        # IaC / Terraform (lettura / inerti)
        "iac.init": partial(iac_tools.init, iac_dir),
        "iac.validate": partial(iac_tools.validate, iac_dir),
        "iac.plan": partial(iac_tools.plan, iac_dir),
        "iac.show": partial(iac_tools.show, iac_dir),
        "iac.state_list": partial(iac_tools.state_list, iac_dir),
        "iac.output": partial(iac_tools.output, iac_dir),
        "iac.write_file": partial(iac_tools.write_file, iac_dir),
        "iac.lint_files": partial(iac_tools.lint_files),
        "iac.generate_moved": partial(iac_tools.generate_moved),
        "resource.list_resources": partial(
            resource_tools.list_resources, resource_mgmt_client
        ),
        "lock.list_locks": partial(lock_tools.list_locks, lock_client),
        "iac.scaffold": partial(iac_tools.scaffold),
        "iac.scaffold_module": partial(iac_tools.scaffold_module),
        "iac.scaffold_environment": partial(iac_tools.scaffold_environment),
        # CI/CD (lettura/inerti)
        "cicd.generate_pipeline": partial(
            cicd_tools.generate_pipeline, cicd_config, str(PROJECT_ROOT)
        ),
        "cicd.get_status": partial(cicd_tools.get_status, cicd_config),
        "cicd.wait_for_run": partial(cicd_tools.wait_for_run, cicd_config),
        "cicd.get_run_jobs": partial(cicd_tools.get_run_jobs, cicd_config),
        "cicd.get_run_logs": partial(cicd_tools.get_run_logs, cicd_config),
        "cicd.render_pipeline": partial(cicd_tools.render_pipeline, cicd_config),
        "cicd.render_pipelines": partial(cicd_tools.render_pipelines, cicd_config),
        "cicd.generate_oidc_federation": partial(cicd_tools.generate_oidc_federation),
        "naming.build": partial(naming_tools.build, naming_config),
        "feasibility.check": partial(feasibility_tools.check),
        "remediation.classify": partial(remediation_tools.classify),
        # File allegati (primitiva upload)
        "files.list_uploads": partial(files_tools.list_uploads, uploads_dir),
        "files.read_upload_text": partial(files_tools.read_upload_text, uploads_dir),
        # Meta-tooling (auto-estensione) — binding base SENZA guardrails/base_dir.
        # Verranno ri-bindati DOPO aver costruito l'oggetto `Guardrails`, così
        # da iniettare la policy (domini riservati + tool nativi) nei meta-tool.
        "meta.list_capabilities": partial(meta_tools.list_capabilities, tool_docs),
        "meta.analyze_request": partial(meta_tools.analyze_request, tool_docs),
        "meta.propose_tool": partial(meta_tools.propose_tool),
        "meta.install_tool": partial(meta_tools.install_tool),
        "meta.list_plugins": partial(meta_tools.list_plugins),
        "meta.remove_plugin": partial(meta_tools.remove_plugin),
        # --- GitHub (lettura remota) ---
        "github.get_default_branch": partial(github_tools.get_default_branch),
        "github.get_file": partial(github_tools.get_file),
        "github.list_directory": partial(github_tools.list_directory),
        "github.get_tree": partial(github_tools.get_tree),
        # NUOVI: lettura cartelle/file remoti in batch — nessuna dipendenza da file locali
        "github.read_folder": partial(github_tools.read_folder),
        "github.get_files": partial(github_tools.get_files),
        "github.search_code": partial(github_tools.search_code),
        "github.get_latest_run": partial(github_tools.get_latest_run),
        "github.get_commit": partial(github_tools.get_commit),
        "github.get_diff": partial(github_tools.get_diff),
        "github.get_workflow": partial(github_tools.get_workflow),
        # Management-plane (lettura)
        "resource.list_resource_groups": partial(
            resource_tools.list_resource_groups, resource_mgmt_client
        ),
        "sql.list_sql_servers": partial(sql_tools.list_sql_servers, sql_mgmt_client),
        "sql.list_sql_databases": partial(
            sql_tools.list_sql_databases, sql_mgmt_client
        ),
        "sql.list_tables": partial(sql_tools.list_tables, sql_conn),
        "sql.get_table_schema": partial(sql_tools.get_table_schema, sql_conn),
        "sql.get_row_count": partial(sql_tools.get_row_count, sql_conn),
        # Connessione a DB arbitrari: connection string chiesta all'operatore.
        "sql.list_tables_for_connection": partial(
            sql_tools.list_tables_for_connection, prompt_secret
        ),
        "sql.get_table_schema_for_connection": partial(
            sql_tools.get_table_schema_for_connection, prompt_secret
        ),
        "sql.get_row_count_for_connection": partial(
            sql_tools.get_row_count_for_connection, prompt_secret
        ),
        # --- SCRITTURA (guardrail: approvazione umana obbligatoria) ---
        "adf.create_linked_service": partial(
            adf_tools.create_linked_service, adf_client, rg, factory, prompt_secret
        ),
        "adf.create_keyvault_linked_service": partial(
            adf_tools.create_keyvault_linked_service, adf_client, rg, factory
        ),
        "adf.create_linked_service_kv": partial(
            adf_tools.create_linked_service_kv, adf_client, rg, factory
        ),
        "adf.create_dataset": partial(adf_tools.create_dataset, adf_client, rg, factory),
        "adf.create_pipeline": partial(adf_tools.create_pipeline, adf_client, rg, factory),
        "adf.trigger_pipeline_run": partial(
            adf_tools.trigger_pipeline_run, adf_client, rg, factory
        ),
        "adf.delete_linked_service": partial(
            adf_tools.delete_linked_service, adf_client, rg, factory
        ),
        "adf.delete_dataset": partial(adf_tools.delete_dataset, adf_client, rg, factory),
        "adf.delete_pipeline": partial(adf_tools.delete_pipeline, adf_client, rg, factory),
        "adf.run_and_wait": partial(adf_tools.run_and_wait, adf_client, rg, factory),
        "adf.create_copy_pipeline": partial(
            adf_tools.create_copy_pipeline, adf_client, rg, factory
        ),
        "sql.execute_write": partial(sql_tools.execute_write, sql_conn),
        "sql.execute_write_for_connection": partial(
            sql_tools.execute_write_for_connection, prompt_secret
        ),
        "blob.upload": partial(blob_tools.upload, blob_client),
        "blob.create_container": partial(blob_tools.create_container, blob_client),
        "blob.delete_container": partial(blob_tools.delete_container, blob_client),
        "table.create_table": partial(table_tools.create_table, table_service),
        "table.delete_table": partial(table_tools.delete_table, table_service),
        "queue.create_queue": partial(queue_tools.create_queue, queue_service),
        "queue.delete_queue": partial(queue_tools.delete_queue, queue_service),
        "fileshare.create_share": partial(fileshare_tools.create_share, share_service),
        "fileshare.create_directory": partial(
            fileshare_tools.create_directory, share_service
        ),
        "fileshare.delete_share": partial(fileshare_tools.delete_share, share_service),
        # Management-plane (creazione risorse di primo livello)
        "resource.create_resource_group": partial(
            resource_tools.create_resource_group, resource_mgmt_client
        ),
        "blob.create_storage_account": partial(
            blob_tools.create_storage_account, storage_mgmt_client
        ),
        "sql.create_sql_server": partial(
            sql_tools.create_sql_server, sql_mgmt_client, prompt_secret
        ),
        "sql.create_sql_database": partial(
            sql_tools.create_sql_database, sql_mgmt_client
        ),
        # ADF Managed VNet / Private Endpoint (scrittura: approvazione umana)
        "adf.create_managed_vnet": partial(
            adf_tools.create_managed_vnet, adf_client, rg, factory
        ),
        "adf.create_managed_private_endpoint": partial(
            adf_tools.create_managed_private_endpoint, adf_client, rg, factory
        ),
        # RBAC (scrittura: approvazione umana)
        "rbac.assign_role": partial(rbac_tools.assign_role, authorization_client),
        "rbac.remove_role_assignment": partial(
            rbac_tools.remove_role_assignment, authorization_client
        ),
        # Key Vault (scrittura: approvazione umana)
        "keyvault.set_secret": partial(
            keyvault_tools.set_secret, credential, prompt_secret
        ),
        "keyvault.delete_secret": partial(keyvault_tools.delete_secret, credential),
        # IaC / Terraform (scrittura: approvazione umana)
        "iac.apply": partial(iac_tools.apply, iac_dir),
        "iac.destroy": partial(iac_tools.destroy, iac_dir),
        "iac.import": partial(iac_tools.import_resource, iac_dir),
        "resource.move_resources": partial(
            resource_tools.move_resources, resource_mgmt_client
        ),
        "lock.create_lock": partial(lock_tools.create_lock, lock_client),
        "lock.delete_lock": partial(lock_tools.delete_lock, lock_client),
        "cicd.trigger_pipeline": partial(cicd_tools.trigger_pipeline, cicd_config),
        "iac.configure_remote_backend": partial(
            iac_tools.configure_remote_backend, iac_dir
        ),
        "github.commit_files": partial(github_tools.commit_files),
        "github.list_branches": partial(github_tools.list_branches),
        "github.create_branch": partial(github_tools.create_branch),
        "github.delete_branch": partial(github_tools.delete_branch),
        "github.create_pull_request": partial(github_tools.create_pull_request),
        "github.merge_pull_request": partial(github_tools.merge_pull_request),
        "github.list_pull_requests": partial(github_tools.list_pull_requests),
        "github.get_pull_request": partial(github_tools.get_pull_request),
        "github.set_secret": partial(github_tools.set_secret, prompt_secret),
    }

    # Anteprime mostrate al gate di approvazione (così l'operatore sa cosa approva).
    previews = {
        "iac.apply": lambda args: iac_tools.show_plan(iac_dir),
        "iac.destroy": lambda args: "Risorse che verranno DISTRUTTE:\n"
        + iac_tools.state_summary(iac_dir),
        "resource.move_resources": lambda args: (
            f"SPOSTA {len(args.get('resource_ids', []))} risorse\n"
            f"  da RG: {args.get('source_resource_group')}\n"
            f"  a  RG: {args.get('target_resource_group')}\n"
            + "\n".join(f"  - {r}" for r in args.get("resource_ids", []))
            + "\nATTENZIONE: PE/DNS/Managed VNet possono rompersi e vanno ricreati."
        ),
        "cicd.trigger_pipeline": lambda args: (
            f"Avvia pipeline CI/CD [{cicd_config.get('provider')}] con comando Terraform "
            f"'{args.get('command', 'plan')}'. "
            + ("APPLY/DESTROY modificano l'infrastruttura reale!" if args.get("command") in ("apply", "destroy") else "")
        ),
        "github.commit_files": lambda args: (
            f"Commit su GitHub (branch {args.get('branch', 'main')}) dei file:\n"
            + "\n".join(f"  - {p}" for p in (args.get("files") or {}))
            + f"\nMessaggio: {args.get('message', '')}"
        ),
        "github.set_secret": lambda args: (
            f"Definizione GitHub Actions secret '{args.get('name')}' (valore inserito "
            "dall'operatore, cifrato, non mostrato)."
        ),
        "meta.install_tool": lambda args: (
            f"INSTALLA nuovo tool '{args.get('name')}' (write={bool(args.get('write'))}) "
            f"nei sorgenti. Il catalogo tool viene ricaricato a caldo subito dopo, senza riavvio.\n"
            + (f"Modifica UI: {args.get('ui')}\n" if args.get("ui") else "")
            + "----- CODICE DEL TOOL -----\n"
            + str(args.get("code", ""))[:4000]
        ),
        "meta.remove_plugin": lambda args: (
            f"RIMUOVE il plugin/tool '{args.get('name')}' dai sorgenti. Il catalogo tool "
            "viene ricaricato a caldo subito dopo, senza riavvio."
        ),
    }

    # --- Fusione tool a MANIFEST (baseline nativa + runtime per-utente) ---
    # I tool dichiarano nome+doc+write: li fondiamo in tools/tool_docs/policy IN MEMORIA,
    # così l'invariante tool↔doc↔policy resta soddisfatta senza modificare cli.py.
    #   - native_plugins/: baseline condivisa (tool promossi a nativi) — sempre caricata;
    #   - plugins/: tool generati a runtime dal singolo utente (isolati) — vedi _user_plugins_dir.
    #
    # SICUREZZA — snapshot dei tool NATIVI:
    #   `_native_tool_names` cattura il set dei nomi PRIMA del merge dei plugin.
    #   Questo è l'insieme che `Guardrails` userà per:
    #     - impedire shadowing (un plugin non può avere il nome di un tool nativo);
    #     - impedire `remove_plugin` su un tool nativo (i nativi sono immutabili).
    #   I plugin caricati DOPO non entrano in questo set: restano modificabili
    #   dall'utente proprietario (li crea, li legge, li modifica, li cancella).
    from hemmy.plugins import load_native_plugins as _load_native
    from hemmy.plugins import load_plugins as _load_plugins
    from hemmy.plugins import load_user_plugins as _load_user
    from hemmy.plugins import user_plugins_dir as _user_pdir

    _native_tool_names: frozenset[str] = frozenset(tools.keys())

    # Se `policy` è vuoto (file mancante o operatore che ha svuotato il YAML),
    # trattiamo tutte le azioni come "scrittura con approvazione": è il default
    # più sicuro (fail-closed). L'agente resta avviabile; l'errore di config
    # è visibile nei log, non blocca l'avvio.
    # --- Normalizzazione difensiva della policy ------------------------------
    # Il YAML può essere: mancante, vuoto, con chiavi a `null`, o con liste
    # svuotate. Normalizziamo SEMPRE prima di usare `policy`, così nessun
    # `setdefault` può ricevere `None` a valle. Le azioni non classificate
    # ricadono nel fallback di `Guardrails.is_write_action` (fail-closed:
    # richiedono approvazione).
    if not isinstance(policy, dict):
        policy = {}

    if not isinstance(policy.get("allowed_actions"), list):
        policy["allowed_actions"] = []

    wa = policy.get("write_actions")
    if not isinstance(wa, dict):
        wa = {}
        policy["write_actions"] = wa
    if not isinstance(wa.get("actions"), list):
        wa["actions"] = []
    # Default sicuro: se il flag non è esplicitamente false, richiedi approvazione.
    if "require_human_approval" not in wa:
        wa["require_human_approval"] = True

    _plugin_write = wa["actions"]
    _plugin_read = policy["allowed_actions"]
    # -------------------------------------------------------------------------
    _merge_specs = [p for p in _load_native() if "error" not in p]
    # Tool runtime: area per-utente se loggato (isolata), altrimenti la cartella condivisa.
    # Prima li rimaterializziamo da Supabase (se configurata): il filesystem è
    # effimero su Cloud Run, quindi ad ogni avvio i tool generati vanno recuperati.
    if user_id is not None:
        from hemmy.plugins import sync_user_plugins_from_db as _sync_user

        try:
            _sync_user(user_id)
        except Exception:  # noqa: BLE001 - non deve impedire l'avvio dell'agente
            pass
        _merge_specs += [p for p in _load_user(user_id) if "error" not in p]
    else:
        from hemmy.plugins import sync_global_plugins_from_db as _sync_global

        try:
            _sync_global()
        except Exception:  # noqa: BLE001
            pass
        _merge_specs += [p for p in _load_plugins() if "error" not in p]
    for _p in _merge_specs:
        _nm = _p["name"]
        if _nm in tools:
            continue  # un nativo "vero" (registrato sopra) ha precedenza sui MANIFEST
        tools[_nm] = partial(_p["func"])
        tool_docs[_nm] = _p.get("doc", "")
        if _p.get("write"):
            if _nm not in _plugin_write:
                _plugin_write.append(_nm)
        elif _nm not in _plugin_read:
            _plugin_read.append(_nm)

    from hemmy.security.anonymizer import Anonymizer

    # --- Costruzione Guardrails (con snapshot dei tool nativi) ---------------
    # `native_tool_names` è il set catturato PRIMA del merge dei plugin:
    # è la lista dei tool immutabili. I plugin caricati dopo non ci sono dentro.
    guardrails = Guardrails(
        policy,
        previews=previews,
        approval_fn=approval_fn,
        native_tool_names=set(_native_tool_names),
    )

    # --- Re-bind dei meta-tool con `guardrails` iniettato --------------------
    # Vengono sovrascritti i binding precedenti (partial senza `guardrails`)
    # per fare in modo che `meta.propose_tool`, `meta.install_tool` e
    # `meta.remove_plugin` applichino le policy di `guardrails.yaml`:
    #   - domini riservati (`filesystem`, `shell`, `os`, …) vietati;
    #   - shadowing di un tool nativo vietato;
    #   - immutabilità dei tool nativi (niente remove);
    #   - scansione statica del codice del plugin (import/call pericolose).
    # `base_dir` e `owner_user_id` restano legati all'area per-utente quando
    # l'agente è costruito per un utente autenticato: i plugin dell'utente
    # restano isolati (`data/plugins/users/<id>/`).
    tools["meta.propose_tool"] = partial(
        meta_tools.propose_tool, guardrails=guardrails
    )
    if user_id is not None:
        _udir = _user_pdir(user_id)
        tools["meta.install_tool"] = partial(
            meta_tools.install_tool,
            base_dir=str(_udir),
            owner_user_id=user_id,
            guardrails=guardrails,
        )
        tools["meta.list_plugins"] = partial(
            meta_tools.list_plugins, base_dir=str(_udir)
        )
        tools["meta.remove_plugin"] = partial(
            meta_tools.remove_plugin,
            base_dir=str(_udir),
            owner_user_id=user_id,
            guardrails=guardrails,
        )
    else:
        tools["meta.install_tool"] = partial(
            meta_tools.install_tool, guardrails=guardrails
        )
        tools["meta.list_plugins"] = partial(meta_tools.list_plugins)
        tools["meta.remove_plugin"] = partial(
            meta_tools.remove_plugin, guardrails=guardrails
        )

    memory = ShortTermMemory()
    from hemmy.audit.db_audit import build_audit_log

    audit = build_audit_log(AUDIT_PATH, user_id=user_id)
    anonymizer = Anonymizer(str(PROJECT_ROOT / ".anonymizer.json"))
    return ADFAgent(
        config,
        prompts,
        tools,
        guardrails,
        memory,
        tool_docs=tool_docs,
        audit=audit,
        docs=docs,
        anonymizer=anonymizer,
        llm_config=llm_config,
    )


def _load_session(agent: Any) -> None:
    """Ripristina la conversazione salvata su disco, se presente."""
    if SESSION_PATH.exists():
        try:
            messages = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            agent.load_conversation(messages)
            print(f"(sessione precedente ripristinata: {len(messages)} messaggi)")
        except (json.JSONDecodeError, OSError):
            pass


def _save_session(agent: Any) -> None:
    """Salva la conversazione corrente su disco."""
    try:
        SESSION_PATH.write_text(
            json.dumps(agent.conversation, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _reset_session(agent: Any) -> None:
    """Azzera il contesto e rimuove il file di sessione."""
    agent.reset_conversation()
    try:
        SESSION_PATH.unlink(missing_ok=True)
    except OSError:
        pass


BACKEND_CONFIG_PATH = CONFIG_DIR / "backend.yaml"


def _ensure_remote_backend() -> dict[str, Any] | None:
    """Passo obbligatorio all'avvio: verifica/《configura》 il remote backend Terraform.

    Se `config/backend.yaml` non esiste, guida la configurazione: chiede nome storage
    account e connection string (input sicuro), CREA il container di state e salva le
    impostazioni non segrete. La connection string NON viene persistita.
    """
    if BACKEND_CONFIG_PATH.exists():
        cfg = yaml.safe_load(BACKEND_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        print(
            f"Remote backend Terraform: {cfg.get('storage_account')}/"
            f"{cfg.get('container')} (key {cfg.get('key')}) ✓"
        )
        return cfg

    print("\n⚠️  Nessun remote backend Terraform configurato — è il passo obbligatorio iniziale.")
    try:
        ans = input("Vuoi configurarlo ora? (si/no): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "no"
    if ans not in {"si", "sì", "s", "y", "yes"}:
        print("   Backend non configurato: le operazioni IaC useranno lo state LOCALE (sconsigliato).")
        return None

    account = input("   Nome storage account per lo state: ").strip()
    resource_group = input("   Resource group dello storage (opzionale): ").strip()
    container = input("   Nome container [tfstate]: ").strip() or "tfstate"

    from hemmy.utils.helpers import prompt_secret

    conn = prompt_secret(f"connection string dello storage account '{account}'")
    if not account or not conn:
        print("   Configurazione annullata.")
        return None

    try:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn)
        try:
            svc.create_container(container)
            print(f"   Container '{container}' creato.")
        except Exception as exc:  # noqa: BLE001 - probabile 'già esistente'
            print(f"   Container non creato (probabilmente già esistente): {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"   Errore nella connessione allo storage: {exc}")
        return None

    cfg = {
        "storage_account": account,
        "resource_group": resource_group,
        "container": container,
        "key": "terraform.tfstate",
    }
    BACKEND_CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"   Backend salvato in {BACKEND_CONFIG_PATH.name}. La connection string NON è stata persistita.")
    return cfg


def _check_azure_auth() -> bool:
    """Verifica di potersi autenticare ad Azure con l'identità personale."""
    try:
        from hemmy.infra import clients

        clients.get_credential().get_token("https://management.azure.com/.default")
        print("  Azure: autenticato ✓ (identità personale)")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  Azure: ✗ — {type(exc).__name__}: {exc}")
        print("     Esegui 'az login' (o completa il login interattivo) e riprova.")
        return False


def _check_cicd_auth(cicd_config: dict[str, Any]) -> bool:
    """Verifica l'autenticazione al provider CI/CD configurato."""
    provider = cicd_config.get("provider", "github")
    try:
        from hemmy.tools.cicd.cicd_tools import _http

        if provider == "github":
            from hemmy.tools.cicd.cicd_tools import resolve_github_token

            token = resolve_github_token()
            if not token:
                raise RuntimeError("non autenticato — esegui 'gh auth login'")
            _, me = _http(
                "GET",
                "https://api.github.com/user",
                {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "adf-agent",
                },
            )
            print(f"  GitHub: autenticato come {me.get('login')} ✓")
            return True
        if provider == "azure_devops":
            org = cicd_config.get("azure_devops", {}).get("organization") or os.environ.get("AZDO_ORG")
            pat = os.environ.get("AZDO_PAT")
            if not org or not pat:
                raise RuntimeError("AZDO_ORG/AZDO_PAT mancanti")
            import base64

            auth = base64.b64encode(f":{pat}".encode()).decode()
            _http(
                "GET",
                f"https://dev.azure.com/{org}/_apis/projects?api-version=7.0",
                {"Authorization": f"Basic {auth}"},
            )
            print("  Azure DevOps: autenticato ✓")
            return True
        raise RuntimeError(f"provider sconosciuto: {provider}")
    except Exception as exc:  # noqa: BLE001
        print(f"  CI/CD ({provider}): ✗ — {exc}")
        print("     Configura il token nel .env e riprova.")
        return False


def _preflight(cicd_config: dict[str, Any]) -> bool:
    """Controlli obbligatori all'avvio: Azure (+backend) e CI/CD.

    Le connessioni (autenticazione) devono esserci per iniziare; i permessi puntuali
    (RBAC) restano responsabilità dell'utente e falliranno correttamente se mancano.
    """
    print("\nVerifica connessioni (identità personale)...")
    azure_ok = _check_azure_auth()
    backend = _ensure_remote_backend()
    cicd_ok = _check_cicd_auth(cicd_config)

    all_ok = azure_ok and (backend is not None) and cicd_ok
    if all_ok:
        print("Tutte le connessioni sono attive. ✓\n")
    else:
        print(
            "\n⚠️  Alcune connessioni mancano. Sistemale prima di operare: le richieste "
            "che le richiedono falliranno finché non sono attive.\n"
        )
    return all_ok


def run_cli() -> None:
    """Ciclo interattivo (o singola domanda) della CLI."""
    parser = argparse.ArgumentParser(description="Agente di Data Engineering su Azure")
    parser.add_argument("-q", "--question", help="Domanda singola (modalità non interattiva)")
    parser.add_argument(
        "--reset", action="store_true", help="Azzera il contesto salvato e avvia pulito"
    )
    args = parser.parse_args()

    agent = _build_agent()

    if args.reset:
        _reset_session(agent)
        print("Contesto azzerato.")
    else:
        _load_session(agent)

    print("Agente pronto.\n")

    if args.question:
        print(agent.run(args.question))
        _save_session(agent)
        return

    # Passi obbligatori all'avvio: autenticazione personale + backend + CI/CD.
    _preflight(_load_cicd())

    print("Hemmy — comandi: 'exit' per uscire, 'reset' per azzerare il contesto.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit"}:
            break
        if question.lower() == "reset":
            _reset_session(agent)
            print("Contesto azzerato.")
            continue
        if question:
            try:
                print("\n" + agent.run(question))
                _save_session(agent)
            except Exception as exc:  # noqa: BLE001 - il REPL non deve morire
                print(f"\n[errore imprevisto] {type(exc).__name__}: {exc}")
                print("La sessione è ancora attiva: riprova.")
                
                
def sync_native_tools_to_db(agent: Any) -> None:
    """Sincronizza il catalogo dei tool nativi su Supabase.

    Chiamata una volta all'avvio del backend (web e CLI). Il portale admin
    legge `native_tools` per mostrare i tool inclusi nel prodotto, separati
    dai plugin utente. Idempotente: upsert per nome.

    Se Supabase non è configurato, non fa nulla (modalità solo-file locale).
    """
    try:
        from hemmy.db.supabase_client import is_supabase_configured, get_admin_client
    except Exception:
        return
    if not is_supabase_configured():
        return

    # Native = tool registrati in TOOL_DOCS + eventuali native_plugins manifest.
    # Un plugin utente NON è nativo anche se compare in agent.tools: la
    # separazione è "nome presente in TOOL_DOCS o caricato da native_plugins".
    native_names: set[str] = set(TOOL_DOCS.keys())
    try:
        from hemmy.plugins import load_native_plugins
        for p in load_native_plugins():
            if "error" not in p and p.get("name"):
                native_names.add(p["name"])
    except Exception:
        pass

    rows = []
    for name in sorted(native_names):
        # Preferiamo la doc del catalogo "vivo" (agent.tool_docs) se presente,
        # altrimenti quella statica in TOOL_DOCS.
        doc = ""
        try:
            doc = str(agent.tool_docs.get(name, "")).replace("[WRITE]", "").strip()
        except Exception:
            doc = str(TOOL_DOCS.get(name, "")).replace("[WRITE]", "").strip()
        try:
            write = bool(agent.guardrails.is_write_action(name))
        except Exception:
            write = "[WRITE]" in str(TOOL_DOCS.get(name, ""))
        rows.append({
            "name": name,
            "domain": name.split(".", 1)[0] if "." in name else name,
            "doc": doc,
            "write": write,
            "is_active": True,
        })

    if not rows:
        return

    try:
        sb = get_admin_client()
        # upsert in blocchi (evita payload troppo grandi)
        for i in range(0, len(rows), 200):
            sb.table("native_tools").upsert(rows[i:i+200], on_conflict="name").execute()
    except Exception as exc:  # noqa: BLE001
        print(f"[sync_native_tools] errore: {type(exc).__name__}: {exc}", file=sys.stderr)                