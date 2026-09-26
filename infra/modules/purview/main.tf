resource "azurerm_purview_account" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  identity {
    type = "SystemAssigned"
  }
}

# Assegna alla Managed Identity di Purview i ruoli di lettura sulle risorse
# da catalogare/scansionare. La MI e' SystemAssigned sull'account Purview.
resource "azurerm_role_assignment" "reader" {
  for_each = var.role_assignments

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_purview_account.this.identity[0].principal_id
}
