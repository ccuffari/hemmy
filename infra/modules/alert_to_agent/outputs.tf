# ---------------------------------------------------------------------------
# Output del modulo alert_to_agent
# ---------------------------------------------------------------------------

output "action_group_id" {
  description = "ID dell'Action Group"
  value       = azurerm_monitor_action_group.this.id
}

output "action_group_name" {
  description = "Nome dell'Action Group"
  value       = azurerm_monitor_action_group.this.name
}

output "metric_alert_id" {
  description = "ID del Metric Alert (se creato)"
  value       = var.alert_type == "metric" ? azurerm_monitor_metric_alert.this[0].id : null
}

output "log_alert_id" {
  description = "ID del Log Alert (se creato)"
  value       = var.alert_type == "log" ? azurerm_monitor_scheduled_query_rules_alert_v2.this[0].id : null
}

output "alert_id" {
  description = "ID dell'alert creato (metric o log)"
  value = (
    var.alert_type == "metric"
    ? azurerm_monitor_metric_alert.this[0].id
    : azurerm_monitor_scheduled_query_rules_alert_v2.this[0].id
  )
}
