"""Tool IaC (Terraform) — provisioning dichiarativo della Data Platform.

Modalità IaC-first: le modifiche strutturali passano da Terraform con flusso
`write_file -> init -> validate -> plan (review) -> apply (approvazione)`.
- Lettura/inerti (no approvazione): init, validate, plan, show, state_list, output,
  write_file (modifica solo file .tf locali, nulla su Azure finché non c'è apply).
- Scrittura (approvazione umana): apply, destroy.

Autenticazione: il provider azurerm usa le variabili ARM_*; qui vengono mappate dalle
AZURE_*/ADF_SUBSCRIPTION_ID già presenti nel .env.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from hemmy.utils.helpers import redact_secrets

_MAX_OUTPUT = 6000


def _env() -> dict[str, str]:
    env = os.environ.copy()
    # Mappa le credenziali Service Principal sul formato atteso da azurerm.
    env.setdefault("ARM_TENANT_ID", os.environ.get("AZURE_TENANT_ID", ""))
    env.setdefault("ARM_CLIENT_ID", os.environ.get("AZURE_CLIENT_ID", ""))
    env.setdefault("ARM_CLIENT_SECRET", os.environ.get("AZURE_CLIENT_SECRET", ""))
    env.setdefault("ARM_SUBSCRIPTION_ID", os.environ.get("ADF_SUBSCRIPTION_ID", ""))
    return env


def _run(workdir: str, args: list[str], timeout: int = 600) -> dict[str, Any]:
    os.makedirs(workdir, exist_ok=True)
    try:
        proc = subprocess.run(
            ["terraform", *args],
            cwd=workdir,
            env=_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Terraform non è installato o non è nel PATH. Installa Terraform "
            "(https://developer.hashicorp.com/terraform/downloads) e riprova."
        ) from exc

    out = redact_secrets((proc.stdout or "")[-_MAX_OUTPUT:])
    err = redact_secrets((proc.stderr or "")[-_MAX_OUTPUT:])
    return {"returncode": proc.returncode, "stdout": out, "stderr": err}


def scaffold(kind: str, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Genera un blocco Terraform (HCL) per una risorsa base, da scrivere con write_file.

    kind: resource_group | storage_account | key_vault | data_factory |
          virtual_network | subnet | sql_server | sql_database.
    Restituisce l'HCL; l'agente lo salva con iac.write_file e poi fa plan/apply via CI/CD.
    """
    p = params or {}
    loc = p.get("location", "westeurope")
    rg = p.get("resource_group_ref", f"azurerm_resource_group.{p.get('rg_name', name)}.name")

    if kind == "resource_group":
        hcl = (
            f'resource "azurerm_resource_group" "{name}" {{\n'
            f'  name     = "{p.get("rg_name", name)}"\n'
            f'  location = "{loc}"\n}}\n'
        )
    elif kind == "storage_account":
        hcl = (
            f'resource "azurerm_storage_account" "{name}" {{\n'
            f'  name                     = "{p.get("account_name", name)}"\n'
            f"  resource_group_name      = {rg}\n"
            f'  location                 = "{loc}"\n'
            f'  account_tier             = "{p.get("tier", "Standard")}"\n'
            f'  account_replication_type = "{p.get("replication", "LRS")}"\n'
            f'  min_tls_version          = "TLS1_2"\n'
            f"  allow_nested_items_to_be_public = false\n}}\n"
        )
    elif kind == "key_vault":
        hcl = (
            f'resource "azurerm_key_vault" "{name}" {{\n'
            f'  name                = "{p.get("vault_name", name)}"\n'
            f"  resource_group_name = {rg}\n"
            f'  location            = "{loc}"\n'
            f"  tenant_id           = var.tenant_id\n"
            f'  sku_name            = "{p.get("sku", "standard")}"\n'
            f"  enable_rbac_authorization  = true\n"
            f"  purge_protection_enabled   = true\n"
            f"  soft_delete_retention_days = 7\n}}\n"
        )
    elif kind == "data_factory":
        hcl = (
            f'resource "azurerm_data_factory" "{name}" {{\n'
            f'  name                = "{p.get("factory_name", name)}"\n'
            f"  resource_group_name = {rg}\n"
            f'  location            = "{loc}"\n'
            f"  identity {{ type = \"SystemAssigned\" }}\n}}\n"
        )
    elif kind == "virtual_network":
        hcl = (
            f'resource "azurerm_virtual_network" "{name}" {{\n'
            f'  name                = "{p.get("vnet_name", name)}"\n'
            f"  resource_group_name = {rg}\n"
            f'  location            = "{loc}"\n'
            f'  address_space       = {p.get("address_space", ["10.0.0.0/16"])!r}\n}}\n'
        ).replace("'", '"')
    elif kind == "subnet":
        hcl = (
            f'resource "azurerm_subnet" "{name}" {{\n'
            f'  name                 = "{p.get("subnet_name", name)}"\n'
            f"  resource_group_name  = {rg}\n"
            f'  virtual_network_name = {p.get("vnet_ref", "azurerm_virtual_network.vnet.name")}\n'
            f'  address_prefixes     = {p.get("address_prefixes", ["10.0.1.0/24"])!r}\n}}\n'
        ).replace("'", '"')
    elif kind == "sql_server":
        # Credenziali SEMPRE da Key Vault: nessuna password in chiaro nel codice.
        kv_id = p.get("key_vault_id", "var.key_vault_id")
        hcl = (
            'data "azurerm_key_vault_secret" "sql_admin_login" {\n'
            f'  name         = "{p.get("admin_login_secret_name", "sql-admin-login")}"\n'
            f"  key_vault_id = {kv_id}\n}}\n\n"
            'data "azurerm_key_vault_secret" "sql_admin_password" {\n'
            f'  name         = "{p.get("admin_password_secret_name", "sql-admin-password")}"\n'
            f"  key_vault_id = {kv_id}\n}}\n\n"
            f'resource "azurerm_mssql_server" "{name}" {{\n'
            f'  name                         = "{p.get("server_name", name)}"\n'
            f"  resource_group_name          = {rg}\n"
            f'  location                     = "{loc}"\n'
            f'  version                      = "12.0"\n'
            "  administrator_login          = data.azurerm_key_vault_secret.sql_admin_login.value\n"
            "  administrator_login_password = data.azurerm_key_vault_secret.sql_admin_password.value\n}\n"
        )
    elif kind == "sql_database":
        hcl = (
            f'resource "azurerm_mssql_database" "{name}" {{\n'
            f'  name      = "{p.get("database_name", name)}"\n'
            f'  server_id = {p.get("server_ref", "azurerm_mssql_server.sql.id")}\n'
            f'  sku_name  = "{p.get("sku", "GP_S_Gen5_1")}"\n}}\n'
        )
    else:
        raise ValueError(f"Tipo scaffold non supportato: '{kind}'.")

    return {"kind": kind, "name": name, "hcl": hcl}


def init(workdir: str) -> dict[str, Any]:
    """Inizializza la working dir Terraform (provider/backend)."""
    return _run(workdir, ["init", "-input=false", "-no-color"])


def validate(workdir: str) -> dict[str, Any]:
    """Valida la configurazione Terraform."""
    return _run(workdir, ["validate", "-no-color"])


def plan(workdir: str) -> dict[str, Any]:
    """Genera il piano (salvato in 'tfplan') e ne riporta il riepilogo per la review."""
    result = _run(workdir, ["plan", "-input=false", "-no-color", "-out=tfplan"])
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    import re

    summary = "nessun riepilogo"
    counts = {"add": 0, "change": 0, "destroy": 0}
    mc = re.search(r"Plan:\s+(\d+) to add,\s+(\d+) to change,\s+(\d+) to destroy", text)
    if mc:
        counts = {
            "add": int(mc.group(1)),
            "change": int(mc.group(2)),
            "destroy": int(mc.group(3)),
        }
    m = re.search(r"Plan:\s+\d+ to add.*destroy\.", text)
    if m:
        summary = m.group(0)
    elif "No changes" in text:
        summary = "No changes. La configurazione è allineata."
    result["summary"] = summary
    result["counts"] = counts
    result["destroy_count"] = counts["destroy"]
    if counts["destroy"] > 0:
        result["destroy_warning"] = (
            f"ATTENZIONE: il piano DISTRUGGE {counts['destroy']} risorsa/e. Se è un refactor "
            "di indirizzo (risorsa spostata in un modulo), NON applicare: usa un blocco "
            "`moved {}` (vedi iac.generate_moved). Applica il destroy solo se è VOLUTO."
        )
    return result


def show(workdir: str) -> dict[str, Any]:
    """Mostra lo stato/piano corrente."""
    return _run(workdir, ["show", "-no-color"])


def state_list(workdir: str) -> dict[str, Any]:
    """Elenca le risorse presenti nello stato Terraform."""
    return _run(workdir, ["state", "list"])


def output(workdir: str) -> dict[str, Any]:
    """Restituisce gli output Terraform (JSON)."""
    return _run(workdir, ["output", "-json", "-no-color"])


def write_file(workdir: str, filename: str, content: str) -> dict[str, Any]:
    """Scrive/aggiorna un file .tf/.tfvars nella working dir (sono ammesse sottocartelle).

    Inerte su Azure finché non viene eseguito `apply`. Vietata la traversal di path.
    """
    norm = filename.replace("\\", "/")
    if os.path.isabs(norm) or ".." in norm.split("/"):
        raise ValueError("Percorso non valido: no path assoluti o '..'.")
    if not (norm.endswith(".tf") or norm.endswith(".tfvars")):
        raise ValueError("Estensione non ammessa: usa .tf o .tfvars.")
    path = os.path.join(workdir, *norm.split("/"))
    os.makedirs(os.path.dirname(path) or workdir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"written": norm, "bytes": len(content.encode("utf-8"))}


# ------------------------------------------------------- lint statico pre-commit

import re as _re

# Stringhe con virgolette doppie (gestisce gli escape): rimosse prima di contare le graffe.
_HCL_STRING_RE = _re.compile(r'"(?:\\.|[^"\\])*"')
# Placeholder/alias residui tipo <res2>, <sa>, <vault1>, <adf1> (NON heredoc `<<`).
_PLACEHOLDER_RE = _re.compile(r"(?<!<)<([A-Za-z][\w .-]{0,38})>(?!>)")
# count basato su una variabile che può essere known-after-apply.
_COUNT_NULL_RE = _re.compile(r"count\s*=\s*var\.\w+\s*==\s*null\s*\?")


def lint_files(files: dict[str, str]) -> dict[str, Any]:
    """Lint statico di file Terraform PRIMA del commit (nessun binario terraform).

    Intercetta gli errori più costosi visti nei deploy reali senza sprecare un giro di
    pipeline:
      - placeholder/alias residui `<...>` nel contenuto (HCL non valido);
      - graffe sbilanciate (sintassi rotta);
      - `count = var.x == null ? ...` (rischio 'Invalid count argument' se x è
        known-after-apply: suggerisce `for_each`);
      - delta intent/implementazione di rete: `public_network_access_enabled = true`
        con firewall deny-all (0.0.0.0) non equivale a public access disabilitato;
        `bypass = AzureServices` con `default_action = Deny` non è "solo VNet".

    Ritorna {ok, errors, warnings, files_checked}. `ok=False` se ci sono errori
    bloccanti: l'agente NON deve committare finché non sono risolti.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for name, content in (files or {}).items():
        text = content or ""

        # 1) placeholder/alias residui -> ERRORE (HCL invalido, deploy destinato a fallire)
        for m in dict.fromkeys(_PLACEHOLDER_RE.findall(text)):
            errors.append(
                f"{name}: placeholder/alias residuo '<{m}>' nel contenuto. Sostituisci con "
                "il nome reale o una variabile (var.<...>) prima del commit."
            )

        # 2) graffe bilanciate (ignorando quelle dentro le stringhe)
        stripped = _HCL_STRING_RE.sub('""', text)
        opened, closed = stripped.count("{"), stripped.count("}")
        if opened != closed:
            errors.append(
                f"{name}: graffe sbilanciate ({opened} '{{' vs {closed} '}}'). "
                "Blocco HCL incompleto."
            )

        # 3) count su variabile potenzialmente known-after-apply -> WARNING (usa for_each)
        if _COUNT_NULL_RE.search(text):
            warnings.append(
                f"{name}: 'count = var.X == null ? ...' può causare 'Invalid count "
                "argument' se X è known-after-apply (es. output di un altro modulo). "
                "Preferisci for_each: `for_each = var.X == null ? {} : {\"k\" = var.X}`."
            )

        # 4) delta intent/implementazione di rete
        low = text.lower()
        if "public_network_access_enabled = true" in low and "0.0.0.0" in text:
            warnings.append(
                f"{name}: delta intent/implementazione — 'public_network_access_enabled = "
                "true' + firewall deny-all (0.0.0.0) NON equivale a public access "
                "disabilitato. Se l'intento è 'niente accesso pubblico', imposta "
                "public_network_access_enabled = false."
            )
        if 'default_action = "deny"' in low and "azureservices" in low:
            warnings.append(
                f"{name}: delta intent/implementazione — con default_action=Deny, "
                "`bypass = AzureServices` consente comunque i servizi Azure: NON è "
                "'solo VNet'. Rimuovi il bypass per accesso esclusivo da VNet."
            )

        # 5) band-aid fragili nei workflow CI (causa dei loop, non soluzione)
        if "-lock=false" in text:
            warnings.append(
                f"{name}: `-lock=false` disabilita il lock dello state (rischioso, può "
                "corrompere lo state con esecuzioni concorrenti). Rimuovilo: se c'è un lease "
                "orfano, rilascialo (break lease) invece di bypassare il lock."
            )
        if "terraform state rm" in low or "terraform import" in low:
            warnings.append(
                f"{name}: `terraform state rm`/`import` a mano in pipeline è un workaround "
                "fragile per un refactor di indirizzo. Usa un blocco `moved {{ }}` nel codice "
                "Terraform (rinomina nello state senza destroy+create)."
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "files_checked": len(files or {}),
    }


# ------------------------------------------------- scaffolding modulare / multi-env

_MODULE_SPECS: dict[str, dict[str, Any]] = {
    "resource_group": {
        "type": "azurerm_resource_group",
        "args": {"name": "var.name", "location": "var.location", "tags": "var.tags"},
        "vars": {"name": "string", "location": "string", "tags": ("map(string)", "{}")},
        "outputs": {"id": ".id", "name": ".name"},
    },
    "storage_account": {
        # Best practice: TLS 1.2 minimo, nessun blob pubblico, accesso pubblico
        # disabilitabile via variabile (default = chiuso).
        "type": "azurerm_storage_account",
        "args": {
            "name": "var.name",
            "resource_group_name": "var.resource_group_name",
            "location": "var.location",
            "account_tier": "var.account_tier",
            "account_replication_type": "var.account_replication_type",
            "min_tls_version": '"TLS1_2"',
            "allow_nested_items_to_be_public": "false",
            "public_network_access_enabled": "var.public_network_access_enabled",
            "tags": "var.tags",
        },
        "vars": {
            "name": "string",
            "resource_group_name": "string",
            "location": "string",
            "account_tier": ("string", "Standard"),
            "account_replication_type": ("string", "LRS"),
            "public_network_access_enabled": ("bool", "false"),
            "tags": ("map(string)", "{}"),
        },
        "outputs": {"id": ".id", "name": ".name", "primary_blob_endpoint": ".primary_blob_endpoint"},
    },
    "key_vault": {
        # Best practice: RBAC authorization, purge protection, soft-delete 7gg,
        # accesso pubblico disabilitabile via variabile (default = chiuso).
        "type": "azurerm_key_vault",
        "args": {
            "name": "var.name",
            "resource_group_name": "var.resource_group_name",
            "location": "var.location",
            "tenant_id": "var.tenant_id",
            "sku_name": "var.sku_name",
            "enable_rbac_authorization": "true",
            "purge_protection_enabled": "true",
            "soft_delete_retention_days": "7",
            "public_network_access_enabled": "var.public_network_access_enabled",
            "tags": "var.tags",
        },
        "vars": {
            "name": "string",
            "resource_group_name": "string",
            "location": "string",
            "tenant_id": "string",
            "sku_name": ("string", "standard"),
            "public_network_access_enabled": ("bool", "false"),
            "tags": ("map(string)", "{}"),
        },
        "outputs": {"id": ".id", "vault_uri": ".vault_uri"},
    },
    "data_factory": {
        "type": "azurerm_data_factory",
        "args": {
            "name": "var.name",
            "resource_group_name": "var.resource_group_name",
            "location": "var.location",
            "tags": "var.tags",
        },
        "vars": {
            "name": "string",
            "resource_group_name": "string",
            "location": "string",
            "tags": ("map(string)", "{}"),
        },
        "outputs": {"id": ".id", "name": ".name"},
    },
    "sql_database": {
        # Best practice: SKU di default 'S0' — provisiona sempre (il serverless
        # GP_S_Gen5_1 richiede min_capacity e può fallire il provisioning).
        "type": "azurerm_mssql_database",
        "args": {"name": "var.name", "server_id": "var.server_id", "sku_name": "var.sku_name"},
        "vars": {"name": "string", "server_id": "string", "sku_name": ("string", "S0")},
        "outputs": {"id": ".id", "name": ".name"},
    },
}


def _var_block(vname: str, vtype: Any) -> str:
    if isinstance(vtype, tuple):
        t, default = vtype
        default_repr = default if t.startswith("map") or default in ("true", "false") else f'"{default}"'
        return f'variable "{vname}" {{\n  type    = {t}\n  default = {default_repr}\n}}\n'
    return f'variable "{vname}" {{\n  type = {vtype}\n}}\n'


def _sql_server_module(name: str) -> dict[str, Any]:
    """Modulo SQL Server con credenziali SEMPRE da Key Vault (mai in chiaro)."""
    main_tf = (
        'data "azurerm_key_vault_secret" "admin_login" {\n'
        "  name         = var.admin_login_secret_name\n"
        "  key_vault_id = var.key_vault_id\n}\n\n"
        'data "azurerm_key_vault_secret" "admin_password" {\n'
        "  name         = var.admin_password_secret_name\n"
        "  key_vault_id = var.key_vault_id\n}\n\n"
        'resource "azurerm_mssql_server" "this" {\n'
        "  name                         = var.name\n"
        "  resource_group_name          = var.resource_group_name\n"
        "  location                     = var.location\n"
        '  version                      = "12.0"\n'
        "  administrator_login          = data.azurerm_key_vault_secret.admin_login.value\n"
        "  administrator_login_password = data.azurerm_key_vault_secret.admin_password.value\n"
        "  tags                         = var.tags\n}\n"
    )
    variables_tf = "\n".join(
        _var_block(v, t)
        for v, t in {
            "name": "string",
            "resource_group_name": "string",
            "location": "string",
            "key_vault_id": "string",
            "admin_login_secret_name": ("string", "sql-admin-login"),
            "admin_password_secret_name": ("string", "sql-admin-password"),
            "tags": ("map(string)", "{}"),
        }.items()
    )
    outputs_tf = (
        'output "id" {\n  value = azurerm_mssql_server.this.id\n}\n\n'
        'output "fully_qualified_domain_name" {\n'
        "  value = azurerm_mssql_server.this.fully_qualified_domain_name\n}\n"
    )
    files = {
        f"modules/{name}/main.tf": main_tf,
        f"modules/{name}/variables.tf": variables_tf,
        f"modules/{name}/outputs.tf": outputs_tf,
    }
    module_call = (
        f'module "{name}" {{\n  source                     = "../../modules/{name}"\n'
        '  name                       = "TODO_server_name"\n'
        "  resource_group_name        = local.resource_group_name\n"
        "  location                   = local.location\n"
        "  key_vault_id               = local.key_vault_id\n"
        '  admin_login_secret_name    = "sql-admin-login"\n'
        '  admin_password_secret_name = "sql-admin-password"\n'
        "  tags                       = local.tags\n}\n"
    )
    return {"files": files, "module_call": module_call}


def _module_files(name: str, main_tf: str, variables_tf: str, outputs_tf: str) -> dict[str, str]:
    return {
        f"modules/{name}/main.tf": main_tf,
        f"modules/{name}/variables.tf": variables_tf,
        f"modules/{name}/outputs.tf": outputs_tf,
    }


def _log_analytics_module(name: str) -> dict[str, Any]:
    """Log Analytics workspace: base dell'observability (sink di log/metriche)."""
    main_tf = (
        'resource "azurerm_log_analytics_workspace" "this" {\n'
        "  name                = var.name\n"
        "  resource_group_name = var.resource_group_name\n"
        "  location            = var.location\n"
        "  sku                 = var.sku\n"
        "  retention_in_days   = var.retention_in_days\n"
        "  tags                = var.tags\n}\n"
    )
    variables_tf = "\n".join(
        _var_block(v, t)
        for v, t in {
            "name": "string",
            "resource_group_name": "string",
            "location": "string",
            "sku": ("string", "PerGB2018"),
            "retention_in_days": ("number", 30),
            "tags": ("map(string)", "{}"),
        }.items()
    )
    outputs_tf = 'output "id" {\n  value = azurerm_log_analytics_workspace.this.id\n}\n'
    call = (
        f'module "{name}" {{\n  source              = "../../modules/{name}"\n'
        "  name                = var.log_analytics_name\n"
        "  resource_group_name = data.azurerm_resource_group.this.name\n"
        "  location            = var.location\n  tags                = local.tags\n}\n"
    )
    return {"files": _module_files(name, main_tf, variables_tf, outputs_tf), "module_call": call}


def _diagnostic_setting_module(name: str) -> dict[str, Any]:
    """Diagnostic setting 'tutte le categorie' → Log Analytics.

    Usa il data source azurerm_monitor_diagnostic_categories + blocchi dynamic: cattura
    automaticamente log e metriche disponibili per la risorsa target, senza elencarle a
    mano (evita categorie inesistenti e drift). Best practice per l'observability.
    """
    main_tf = (
        'data "azurerm_monitor_diagnostic_categories" "this" {\n'
        "  resource_id = var.target_resource_id\n}\n\n"
        'resource "azurerm_monitor_diagnostic_setting" "this" {\n'
        "  name                       = var.name\n"
        "  target_resource_id         = var.target_resource_id\n"
        "  log_analytics_workspace_id = var.log_analytics_workspace_id\n\n"
        '  dynamic "enabled_log" {\n'
        "    for_each = data.azurerm_monitor_diagnostic_categories.this.log_category_types\n"
        "    content {\n      category = enabled_log.value\n    }\n  }\n\n"
        '  dynamic "metric" {\n'
        "    for_each = data.azurerm_monitor_diagnostic_categories.this.metrics\n"
        "    content {\n      category = metric.value\n    }\n  }\n}\n"
    )
    variables_tf = "\n".join(
        _var_block(v, t)
        for v, t in {
            "name": "string",
            "target_resource_id": "string",
            "log_analytics_workspace_id": "string",
        }.items()
    )
    outputs_tf = 'output "id" {\n  value = azurerm_monitor_diagnostic_setting.this.id\n}\n'
    call = (
        f'module "{name}" {{\n  source                     = "../../modules/{name}"\n'
        '  name                       = "diag-TODO"\n'
        "  target_resource_id         = module.TODO.id\n"
        "  log_analytics_workspace_id = module.log_analytics.id\n}\n"
    )
    return {"files": _module_files(name, main_tf, variables_tf, outputs_tf), "module_call": call}


def _action_group_module(name: str) -> dict[str, Any]:
    """Action group per gli alert (destinatari email)."""
    main_tf = (
        'resource "azurerm_monitor_action_group" "this" {\n'
        "  name                = var.name\n"
        "  resource_group_name = var.resource_group_name\n"
        "  short_name          = var.short_name\n\n"
        '  dynamic "email_receiver" {\n'
        "    for_each = var.email_receivers\n"
        "    content {\n"
        "      name          = email_receiver.value.name\n"
        "      email_address = email_receiver.value.email\n"
        "    }\n  }\n\n  tags = var.tags\n}\n"
    )
    variables_tf = (
        'variable "name" {\n  type = string\n}\n\n'
        'variable "resource_group_name" {\n  type = string\n}\n\n'
        'variable "short_name" {\n  type = string\n}\n\n'
        'variable "email_receivers" {\n'
        "  type    = list(object({ name = string, email = string }))\n"
        "  default = []\n}\n\n"
        'variable "tags" {\n  type    = map(string)\n  default = {}\n}\n'
    )
    outputs_tf = 'output "id" {\n  value = azurerm_monitor_action_group.this.id\n}\n'
    call = (
        f'module "{name}" {{\n  source              = "../../modules/{name}"\n'
        '  name                = "ag-TODO"\n'
        "  resource_group_name = data.azurerm_resource_group.this.name\n"
        '  short_name          = "TODO"\n'
        "  email_receivers     = var.alert_email_receivers\n  tags = local.tags\n}\n"
    )
    return {"files": _module_files(name, main_tf, variables_tf, outputs_tf), "module_call": call}


def _metric_alert_module(name: str) -> dict[str, Any]:
    """Metric alert su una risorsa, collegato a un action group."""
    main_tf = (
        'resource "azurerm_monitor_metric_alert" "this" {\n'
        "  name                = var.name\n"
        "  resource_group_name = var.resource_group_name\n"
        "  scopes              = var.scopes\n"
        "  description         = var.description\n"
        "  severity            = var.severity\n\n"
        "  criteria {\n"
        "    metric_namespace = var.metric_namespace\n"
        "    metric_name      = var.metric_name\n"
        "    aggregation      = var.aggregation\n"
        "    operator         = var.operator\n"
        "    threshold        = var.threshold\n  }\n\n"
        "  action {\n    action_group_id = var.action_group_id\n  }\n\n  tags = var.tags\n}\n"
    )
    variables_tf = "\n".join(
        _var_block(v, t)
        for v, t in {
            "name": "string",
            "resource_group_name": "string",
            "scopes": "list(string)",
            "description": ("string", "Metric alert gestito da Terraform"),
            "severity": ("number", 3),
            "metric_namespace": "string",
            "metric_name": "string",
            "aggregation": ("string", "Average"),
            "operator": ("string", "GreaterThan"),
            "threshold": ("number", 80),
            "action_group_id": "string",
            "tags": ("map(string)", "{}"),
        }.items()
    )
    outputs_tf = 'output "id" {\n  value = azurerm_monitor_metric_alert.this.id\n}\n'
    call = (
        f'module "{name}" {{\n  source              = "../../modules/{name}"\n'
        '  name                = "alert-TODO"\n'
        "  resource_group_name = data.azurerm_resource_group.this.name\n"
        "  scopes              = [module.TODO.id]\n"
        "  metric_namespace    = \"TODO\"\n  metric_name = \"TODO\"\n"
        "  action_group_id     = module.action_group.id\n  tags = local.tags\n}\n"
    )
    return {"files": _module_files(name, main_tf, variables_tf, outputs_tf), "module_call": call}


def _budget_module(name: str) -> dict[str, Any]:
    """Consumption budget sul resource group con notifiche a soglie (cost management)."""
    main_tf = (
        'resource "azurerm_consumption_budget_resource_group" "this" {\n'
        "  name              = var.name\n"
        "  resource_group_id = var.resource_group_id\n"
        "  amount            = var.amount\n"
        "  time_grain        = var.time_grain\n\n"
        "  time_period {\n    start_date = var.start_date\n  }\n\n"
        '  dynamic "notification" {\n'
        "    for_each = var.threshold_percentages\n"
        "    content {\n"
        "      enabled        = true\n"
        "      threshold      = notification.value\n"
        '      operator       = "GreaterThanOrEqualTo"\n'
        "      contact_emails = var.contact_emails\n"
        "    }\n  }\n}\n"
    )
    variables_tf = (
        'variable "name" {\n  type = string\n}\n\n'
        'variable "resource_group_id" {\n  type = string\n}\n\n'
        'variable "amount" {\n  type = number\n}\n\n'
        'variable "time_grain" {\n  type    = string\n  default = "Monthly"\n}\n\n'
        'variable "start_date" {\n  type = string\n}\n\n'
        'variable "threshold_percentages" {\n  type    = list(number)\n  default = [80, 100]\n}\n\n'
        'variable "contact_emails" {\n  type    = list(string)\n  default = []\n}\n'
    )
    outputs_tf = 'output "id" {\n  value = azurerm_consumption_budget_resource_group.this.id\n}\n'
    call = (
        f'module "{name}" {{\n  source            = "../../modules/{name}"\n'
        '  name              = "budget-TODO"\n'
        "  resource_group_id = data.azurerm_resource_group.this.id\n"
        "  amount            = var.budget_amount\n"
        '  start_date        = "2026-01-01T00:00:00Z"\n'
        "  contact_emails    = var.budget_contact_emails\n}\n"
    )
    return {"files": _module_files(name, main_tf, variables_tf, outputs_tf), "module_call": call}


# Builder dedicati (blocchi nested/dynamic non esprimibili da _MODULE_SPECS piatto).
_SPECIAL_BUILDERS = {
    "sql_server": _sql_server_module,
    "log_analytics": _log_analytics_module,
    "diagnostic_setting": _diagnostic_setting_module,
    "action_group": _action_group_module,
    "metric_alert": _metric_alert_module,
    "budget": _budget_module,
}


def scaffold_module(kind: str, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Genera un MODULO Terraform (main/variables/outputs) per una risorsa.

    Ritorna {"files": {path: content}} sotto modules/<name>/ e un "module_call" pronto
    da inserire nel main.tf dell'ambiente. I segreti (es. credenziali SQL) sono SEMPRE
    referenziati da Key Vault, mai in chiaro. kind: resource_group | storage_account |
    key_vault | data_factory | sql_server | sql_database | log_analytics |
    diagnostic_setting | action_group | metric_alert | budget.
    """
    builder = _SPECIAL_BUILDERS.get(kind)
    if builder is not None:
        return builder(name)

    spec = _MODULE_SPECS.get(kind)
    if not spec:
        available = sorted(set(_MODULE_SPECS) | set(_SPECIAL_BUILDERS))
        raise ValueError(
            f"Tipo modulo non supportato: '{kind}'. Disponibili: {available}."
        )
    rtype = spec["type"]
    params = params or {}

    args_lines = "\n".join(f"  {k:<26} = {v}" for k, v in spec["args"].items())
    main_tf = f'resource "{rtype}" "this" {{\n{args_lines}\n}}\n'
    variables_tf = "\n".join(_var_block(v, t) for v, t in spec["vars"].items())
    outputs_tf = "\n".join(
        f'output "{o}" {{\n  value = {rtype}.this{attr}\n}}\n' for o, attr in spec["outputs"].items()
    )

    files = {
        f"modules/{name}/main.tf": main_tf,
        f"modules/{name}/variables.tf": variables_tf,
        f"modules/{name}/outputs.tf": outputs_tf,
    }

    def _call_val(var: str) -> str:
        if var in params:
            val = params[var]
            return val if var == "tags" else f'"{val}"'
        if var == "location":
            return "local.location"
        if var == "tags":
            return "local.tags"
        return f'"TODO_{var}"'

    call_lines = "\n".join(f"  {v:<26} = {_call_val(v)}" for v in spec["vars"])
    module_call = f'module "{name}" {{\n  source = "../../modules/{name}"\n{call_lines}\n}}\n'
    return {"files": files, "module_call": module_call}


def scaffold_environment(
    env: str,
    storage_account: str = "<storage_account>",
    container: str = "tfstate",
    resource_group: str = "<rg>",
    location: str = "westeurope",
) -> dict[str, Any]:
    """Genera lo scheletro di un AMBIENTE (dev/uat/prod): providers, backend, locals,
    variables, main (composizione moduli), tfvars."""
    providers = (
        "terraform {\n"
        '  required_version = ">= 1.5.0"\n'
        "  required_providers {\n    azurerm = {\n"
        '      source  = "hashicorp/azurerm"\n      version = "~> 3.100"\n    }\n  }\n}\n\n'
        'provider "azurerm" {\n  features {}\n}\n'
    )
    backend = (
        "terraform {\n  backend \"azurerm\" {\n"
        f'    resource_group_name  = "{resource_group}"\n'
        f'    storage_account_name = "{storage_account}"\n'
        f'    container_name       = "{container}"\n'
        f'    key                  = "{env}/terraform.tfstate"\n'
        "    use_azuread_auth     = true\n  }\n}\n"
    )
    locals_tf = (
        "locals {\n"
        f'  environment = "{env}"\n'
        f'  location    = "{location}"\n'
        "  tags = {\n"
        f'    environment = "{env}"\n'
        '    managed_by  = "terraform"\n'
        "  }\n}\n"
    )
    variables_tf = f'variable "location" {{\n  type    = string\n  default = "{location}"\n}}\n'
    main_tf = (
        f"# Ambiente {env}: componi qui i moduli.\n"
        "#\n"
        "# BEST PRACTICE - refactor di indirizzo (es. risorsa spostata dentro un modulo):\n"
        "# usa un blocco `moved { from = ...  to = ... }`, MAI destroy+create.\n"
        "# NON usare `terraform state rm` / `terraform import` / `-lock=false` come workaround.\n"
        "#\n"
        '# Esempio:\n# module "rg_demo" {\n#   source   = "../../modules/rg_demo"\n'
        "#   name     = \"rg-demo\"\n#   location = local.location\n#   tags     = local.tags\n# }\n"
    )
    tfvars = f'location = "{location}"\n'
    files = {
        f"environments/{env}/providers.tf": providers,
        f"environments/{env}/backend.tf": backend,
        f"environments/{env}/locals.tf": locals_tf,
        f"environments/{env}/variables.tf": variables_tf,
        f"environments/{env}/main.tf": main_tf,
        f"environments/{env}/terraform.tfvars": tfvars,
    }
    return {"environment": env, "files": files}


def show_plan(workdir: str) -> str:
    """Restituisce il piano salvato in forma leggibile (per l'anteprima al gate)."""
    if not os.path.exists(os.path.join(workdir, "tfplan")):
        return "Nessun piano salvato: esegui prima 'iac.plan'."
    result = _run(workdir, ["show", "-no-color", "tfplan"])
    return (result.get("stdout") or result.get("stderr") or "")[:_MAX_OUTPUT]


def state_summary(workdir: str) -> str:
    """Elenco delle risorse nello stato (per l'anteprima di destroy)."""
    result = _run(workdir, ["state", "list"])
    return (result.get("stdout") or "(stato vuoto)").strip()[:_MAX_OUTPUT]


def plan_destroys(workdir: str) -> int:
    """Numero di risorse che il piano salvato ('tfplan') DISTRUGGEREBBE.

    Ritorna -1 se non esiste un piano salvato. Usato dalla guardia anti-destroy di
    apply per bloccare distruzioni non attese (tipico dei refactor di indirizzo).
    """
    if not os.path.exists(os.path.join(workdir, "tfplan")):
        return -1
    import re

    result = _run(workdir, ["show", "-no-color", "tfplan"])
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    m = re.search(r"Plan:\s+\d+ to add,\s+\d+ to change,\s+(\d+) to destroy", text)
    if m:
        return int(m.group(1))
    return len(re.findall(r"will be destroyed", text))


def apply(workdir: str, confirm_destroy: bool = False) -> dict[str, Any]:
    """[WRITE] Applica il piano salvato ('tfplan'). Richiede una plan precedente.

    Guardia anti-destroy: se il piano distrugge risorse e `confirm_destroy` è False,
    l'apply viene BLOCCATO (un destroy non atteso è spesso un refactor di indirizzo da
    gestire con `moved {}`, oppure un drift). Per distruggere di proposito: confirm_destroy=true.
    """
    if not os.path.exists(os.path.join(workdir, "tfplan")):
        raise RuntimeError(
            "Nessun piano salvato: esegui prima 'iac.plan' e fai revisionare l'output."
        )
    if not confirm_destroy:
        n = plan_destroys(workdir)
        if n > 0:
            raise RuntimeError(
                f"APPLY BLOCCATO: il piano distruggerebbe {n} risorsa/e. Un destroy non "
                "atteso di solito è un refactor di indirizzo (usa un blocco `moved {}` via "
                "iac.generate_moved, che rinomina nello state senza destroy+create) oppure "
                "un drift. Verifica il piano con iac.show. Se il destroy è VOLUTO, riesegui "
                "iac.apply con confirm_destroy=true."
            )
    return _run(workdir, ["apply", "-input=false", "-no-color", "tfplan"])


def generate_moved(moves: list[dict[str, str]], filename: str = "moved.tf") -> dict[str, Any]:
    """Genera blocchi `moved {}` per un refactor di indirizzo Terraform.

    È l'alternativa CORRETTA a destroy+create (e ai band-aid `state rm`/`import` in
    pipeline) quando una risorsa cambia indirizzo — es. spostata dentro un modulo:
    da `azurerm_x.foo` a `module.foo.azurerm_x.this`. Terraform rinomina la risorsa
    nello state senza distruggerla/ricrearla, evitando le destroy che si impiantano.

    `moves`: lista di {"from": "<indirizzo_vecchio>", "to": "<indirizzo_nuovo>"}.
    Gli indirizzi sono identificatori HCL (NON stringhe): niente virgolette.
    Ritorna {filename, content, files: {filename: content}, moves_count}, pronto per
    iac.write_file o github.commit_files.
    """
    if not moves:
        raise ValueError(
            "Nessun move fornito: passa una lista di {'from': <addr>, 'to': <addr>}."
        )
    blocks: list[str] = []
    for mv in moves:
        frm = str((mv or {}).get("from", "")).strip()
        to = str((mv or {}).get("to", "")).strip()
        if not frm or not to:
            raise ValueError(
                f"Move non valido: servono 'from' e 'to' non vuoti (ricevuto {mv})."
            )
        blocks.append(f"moved {{\n  from = {frm}\n  to   = {to}\n}}\n")
    content = (
        "# Refactor di indirizzo (rinomina nello state, NO destroy+create).\n"
        "# Generato da iac.generate_moved. Rimuovibile dopo un apply riuscito.\n\n"
        + "\n".join(blocks)
    )
    return {
        "filename": filename,
        "content": content,
        "files": {filename: content},
        "moves_count": len(blocks),
    }


def destroy(workdir: str) -> dict[str, Any]:
    """[WRITE] Distrugge le risorse gestite da questa configurazione Terraform."""
    return _run(workdir, ["destroy", "-input=false", "-no-color", "-auto-approve"])


def _backend_hcl(
    resource_group: str, storage_account: str, container: str, key: str
) -> str:
    return (
        "terraform {\n"
        '  backend "azurerm" {\n'
        f'    resource_group_name  = "{resource_group}"\n'
        f'    storage_account_name = "{storage_account}"\n'
        f'    container_name       = "{container}"\n'
        f'    key                  = "{key}"\n'
        "    use_azuread_auth     = true\n"
        "  }\n}\n"
    )


def configure_remote_backend(
    workdir: str,
    resource_group: str,
    storage_account: str,
    container: str,
    key: str = "terraform.tfstate",
) -> dict[str, Any]:
    """[WRITE] Configura il backend remoto azurerm e migra lo state nello Storage.

    Scrive backend.tf e lancia `terraform init -migrate-state`. Prerequisiti: lo
    storage account e il container devono già esistere, e il Service Principal deve
    avere 'Storage Blob Data Contributor' sul container (use_azuread_auth).
    """
    content = _backend_hcl(resource_group, storage_account, container, key)
    path = os.path.join(workdir, "backend.tf")
    os.makedirs(workdir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    result = _run(
        workdir, ["init", "-input=false", "-no-color", "-force-copy", "-migrate-state"]
    )
    result["backend"] = {
        "storage_account": storage_account,
        "container": container,
        "key": key,
    }
    return result


def import_resource(workdir: str, address: str, resource_id: str) -> dict[str, Any]:
    """[WRITE] Importa una risorsa Azure ESISTENTE nello state Terraform.

    Riconcilia lo state con risorse già presenti (es. dopo un apply fallito o risorse
    create fuori da Terraform). Il blocco di configurazione corrispondente ad `address`
    deve già esistere nei .tf. NON modifica la risorsa su Azure, solo lo state locale.
    - address: indirizzo Terraform, es. azurerm_resource_group.rg
    - resource_id: resource id Azure completo
    """
    return _run(workdir, ["import", "-input=false", "-no-color", address, resource_id])
