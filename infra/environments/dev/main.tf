module "resource_group" {
  source   = "../../modules/resource_group"
  name     = local.resource_group_name
  location = local.location
  tags     = local.tags
}

module "virtual_network" {
  source              = "../../modules/virtual_network"
  name                = local.vnet_name
  resource_group_name = module.resource_group.name
  location            = local.location
  address_space       = ["10.0.0.0/16"]
  subnet_name         = local.subnet_name
  subnet_prefixes     = ["10.0.1.0/24"]
  service_endpoints   = ["Microsoft.Storage", "Microsoft.KeyVault", "Microsoft.Sql"]
  tags                = local.tags
}

module "storage_account" {
  source                   = "../../modules/storage_account"
  name                     = local.storage_account_name
  resource_group_name      = module.resource_group.name
  location                 = local.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  network_default_action   = "Deny"
  allowed_subnet_ids       = [module.virtual_network.subnet_id]
  allowed_ip_addresses     = var.allowed_ip_addresses
  tags                     = local.tags
}

module "storage_account_02" {
  source                   = "../../modules/storage_account"
  name                     = local.storage_account_02_name
  resource_group_name      = module.resource_group.name
  location                 = local.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  network_default_action   = "Deny"
  allowed_subnet_ids       = [module.virtual_network.subnet_id]
  allowed_ip_addresses     = var.allowed_ip_addresses
  tags                     = local.tags
}

module "container_bronze" {
  source             = "../../modules/storage_container"
  name               = "bronze"
  storage_account_id = module.storage_account.id
}

module "container_silver" {
  source             = "../../modules/storage_container"
  name               = "silver"
  storage_account_id = module.storage_account.id
}

module "container_gold" {
  source             = "../../modules/storage_container"
  name               = "gold"
  storage_account_id = module.storage_account.id
}

module "container_landing" {
  source             = "../../modules/storage_container"
  name               = "landing"
  storage_account_id = module.storage_account.id
}

module "data_factory" {
  source              = "../../modules/data_factory"
  name                = local.data_factory_name
  resource_group_name = module.resource_group.name
  location            = local.location
  tags                = local.tags
}

module "databricks" {
  source              = "../../modules/databricks"
  name                = local.databricks_name
  resource_group_name = module.resource_group.name
  location            = local.location
  sku                 = "premium"
  tags                = local.tags
}

module "key_vault" {
  source                 = "../../modules/key_vault"
  name                   = local.key_vault_name
  resource_group_name    = module.resource_group.name
  location               = local.location
  tenant_id              = local.tenant_id
  sku_name               = "standard"
  network_default_action = "Deny"
  allowed_subnet_ids     = [module.virtual_network.subnet_id]
  allowed_ip_addresses   = var.allowed_ip_addresses
  tags                   = local.tags
}

module "log_analytics" {
  source              = "../../modules/log_analytics"
  name                = local.log_analytics_name
  resource_group_name = module.resource_group.name
  location            = local.location
  tags                = local.tags
}

module "sql_server" {
  source              = "../../modules/sql_server"
  name                = local.sql_server_name
  resource_group_name = module.resource_group.name
  location            = local.location
  admin_login         = var.sql_admin_login
  admin_password      = var.sql_admin_password
  subnet_id           = module.virtual_network.subnet_id
  enable_vnet_rule    = true
  tags                = local.tags
}

module "sql_database" {
  source    = "../../modules/sql_database"
  name      = local.sql_database_name
  server_id = module.sql_server.id
  sku_name  = "S0"
  tags      = local.tags
}

# --- Azure Synapse Analytics ---
# Workspace Synapse con data lake ADLS Gen2 dedicato e Managed VNet.
module "synapse" {
  source                        = "../../modules/synapse"
  name                          = local.synapse_name
  resource_group_name           = module.resource_group.name
  location                      = local.location
  datalake_storage_account_name = local.synapse_datalake_name
  datalake_filesystem_name      = "synapse"
  sql_administrator_login       = var.sql_admin_login
  sql_administrator_login_password = var.sql_admin_password
  managed_virtual_network_enabled  = true
  network_default_action        = "Deny"
  allowed_subnet_ids            = [module.virtual_network.subnet_id]
  allowed_ip_addresses          = var.allowed_ip_addresses
  tags                          = local.tags
}

module "budget" {
  source            = "../../modules/budget"
  name              = local.budget_name
  resource_group_id = module.resource_group.id
  amount            = var.budget_amount
  start_date        = var.budget_start_date
  contact_emails    = var.budget_contact_emails
  notifications = [
    { threshold = 50, operator = "GreaterThan", threshold_type = "Actual" },
    { threshold = 80, operator = "GreaterThan", threshold_type = "Actual" },
    { threshold = 100, operator = "GreaterThan", threshold_type = "Actual" },
  ]
}

module "diag_storage_account" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-storage-account"
  target_resource_id         = module.storage_account.id
  log_analytics_workspace_id = module.log_analytics.id
  metrics                    = ["AllMetrics"]
}

module "diag_storage_account_02" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-storage-account-02"
  target_resource_id         = module.storage_account_02.id
  log_analytics_workspace_id = module.log_analytics.id
  metrics                    = ["AllMetrics"]
}

module "diag_adf" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-adf"
  target_resource_id         = module.data_factory.id
  log_analytics_workspace_id = module.log_analytics.id
  enabled_logs               = ["PipelineRuns", "ActivityRuns", "TriggerRuns"]
  metrics                    = ["AllMetrics"]
}

module "diag_databricks" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-databricks"
  target_resource_id         = module.databricks.id
  log_analytics_workspace_id = module.log_analytics.id
  metrics                    = ["AllMetrics"]
}

module "diag_key_vault" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-keyvault"
  target_resource_id         = module.key_vault.id
  log_analytics_workspace_id = module.log_analytics.id
  enabled_logs               = ["AuditEvent"]
  metrics                    = ["AllMetrics"]
}

module "diag_sql_server" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-sqlserver"
  target_resource_id         = module.sql_server.id
  log_analytics_workspace_id = module.log_analytics.id
  metrics                    = ["AllMetrics"]
}

module "diag_sql_database" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-sqldb"
  target_resource_id         = module.sql_database.id
  log_analytics_workspace_id = module.log_analytics.id
  enabled_logs = [
    "SQLInsights",
    "Errors",
    "Timeouts",
    "Blocks",
    "Deadlocks",
    "QueryStoreRuntimeStatistics",
    "QueryStoreWaitStatistics",
    "DatabaseWaitStatistics",
  ]
  metrics = ["AllMetrics"]
}

module "diag_synapse" {
  source                     = "../../modules/diagnostic_settings"
  name                       = "diag-synapse"
  target_resource_id         = module.synapse.id
  log_analytics_workspace_id = module.log_analytics.id
  metrics                    = ["AllMetrics"]
}

module "alert_sql_dtu" {
  source              = "../../modules/alert_to_agent"
  name                = "alert-sql-dtu-high"
  resource_group_name = module.resource_group.name
  short_name          = "sql-dtu"
  target_resource_id  = module.sql_database.id
  metric_namespace    = "Microsoft.Sql/servers/databases"
  metric_name         = "dtu_consumption_percent"
  aggregation         = "Average"
  operator            = "GreaterThan"
  threshold           = 80
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT5M"
  description         = "DTU del database SQL sopra l'80% - invoca l'agente per diagnosi"
  agent_webhook_url   = var.agent_webhook_url
  email_receivers     = var.alert_email_receivers
  tags                = local.tags
}

removed {
  from = module.container_bronze.azurerm_storage_container.this
  lifecycle {
    destroy = false
  }
}

removed {
  from = module.container_silver.azurerm_storage_container.this
  lifecycle {
    destroy = false
  }
}

removed {
  from = module.container_gold.azurerm_storage_container.this
  lifecycle {
    destroy = false
  }
}

removed {
  from = module.container_landing.azurerm_storage_container.this
  lifecycle {
    destroy = false
  }
}
