# ---------------------------------------------------------------------------
# Modulo: alert_to_agent
#
# Crea un alert Azure Monitor (metric o log) che, quando scatta, invoca
# l'agente via webhook HTTPS (Action Group).
#
# Componenti:
#   - azurerm_monitor_action_group  (webhook verso l'agente)
#   - azurerm_monitor_metric_alert  (alert su metrica)
#   - azurerm_monitor_scheduled_query_rules_alert_v2 (opzionale, log alert)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Action Group: webhook verso l'agente
# ---------------------------------------------------------------------------
resource "azurerm_monitor_action_group" "this" {
  name                = var.action_group_name
  resource_group_name = var.resource_group_name
  short_name          = var.action_group_short_name

  dynamic "email_receiver" {
    for_each = var.email_receivers
    content {
      name          = email_receiver.value.name
      email_address = email_receiver.value.email
    }
  }

  dynamic "webhook_receiver" {
    for_each = var.agent_webhook_url != null ? [1] : []
    content {
      name                    = "agent-webhook"
      service_uri             = var.agent_webhook_url
      use_common_alert_schema = true

      # Header di autenticazione (Bearer token)
      dynamic "aad_auth" {
        for_each = []
        content {}
      }
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Metric Alert
# ---------------------------------------------------------------------------
resource "azurerm_monitor_metric_alert" "this" {
  count = var.alert_type == "metric" ? 1 : 0

  name                = var.name
  resource_group_name = var.resource_group_name
  scopes              = [var.target_resource_id]
  description         = var.description
  severity            = var.severity
  enabled             = var.enabled
  frequency           = var.frequency
  window_size         = var.window_size

  criteria {
    metric_namespace = var.metric_namespace
    metric_name      = var.metric_name
    aggregation      = var.aggregation
    operator         = var.operator
    threshold        = var.threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.this.id
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Log Alert (scheduled query rule) - opzionale
# ---------------------------------------------------------------------------
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "this" {
  count = var.alert_type == "log" ? 1 : 0

  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  description         = var.description
  severity            = var.severity
  enabled             = var.enabled
  evaluation_frequency = var.frequency
  window_duration      = var.window_size
  scopes               = [var.log_analytics_workspace_id]

  criteria {
    query                   = var.log_query
    time_aggregation_method = var.aggregation
    threshold               = var.threshold
    operator                = var.operator

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }

  tags = var.tags
}
