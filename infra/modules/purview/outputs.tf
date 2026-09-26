output "id" {
  value = azurerm_purview_account.this.id
}

output "name" {
  value = azurerm_purview_account.this.name
}

output "identity_principal_id" {
  value = azurerm_purview_account.this.identity[0].principal_id
}
