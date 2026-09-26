output "id" {
  description = "ID del Synapse workspace."
  value       = azurerm_synapse_workspace.this.id
}

output "name" {
  description = "Nome del Synapse workspace."
  value       = azurerm_synapse_workspace.this.name
}

output "connectivity_endpoints" {
  description = "Endpoint di connettività del workspace (SQL, Dev, ecc.)."
  value       = azurerm_synapse_workspace.this.connectivity_endpoints
}

output "identity_principal_id" {
  description = "Principal ID della Managed Identity del workspace (per RBAC)."
  value       = azurerm_synapse_workspace.this.identity[0].principal_id
}

output "datalake_storage_account_id" {
  description = "ID dello storage account ADLS Gen2."
  value       = azurerm_storage_account.datalake.id
}

output "datalake_filesystem_id" {
  description = "ID del filesystem ADLS Gen2."
  value       = azurerm_storage_data_lake_gen2_filesystem.this.id
}
