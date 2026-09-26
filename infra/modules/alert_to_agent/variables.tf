# ---------------------------------------------------------------------------
# Variabili del modulo alert_to_agent
# ---------------------------------------------------------------------------

# --- Generali ---
variable "name" {
  description = "Nome dell'alert"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group di destinazione"
  type        = string
}

variable "location" {
  description = "Region Azure (per log alert)"
  type        = string
  default     = "westeurope"
}

variable "description" {
  description = "Descrizione dell'alert"
  type        = string
  default     = "Alert che invoca l'agente per la diagnosi"
}

variable "enabled" {
  description = "Alert abilitato"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tag da applicare alle risorse"
  type        = map(string)
  default     = {}
}

# --- Action Group ---
variable "action_group_name" {
  description = "Nome dell'Action Group"
  type        = string
}

variable "action_group_short_name" {
  description = "Short name dell'Action Group (max 12 caratteri)"
  type        = string
}

variable "email_receivers" {
  description = "Lista di receiver email"
  type        = list(object({ name = string, email = string }))
  default     = []
}

variable "agent_webhook_url" {
  description = "URL del webhook dell'agente (da Key Vault)"
  type        = string
  sensitive   = true
  default     = null
}

# --- Alert ---
variable "alert_type" {
  description = "Tipo di alert: 'metric' o 'log'"
  type        = string
  default     = "metric"

  validation {
    condition     = contains(["metric", "log"], var.alert_type)
    error_message = "alert_type deve essere 'metric' o 'log'"
  }
}

variable "target_resource_id" {
  description = "ID della risorsa da monitorare (per metric alert)"
  type        = string
  default     = null
}

variable "log_analytics_workspace_id" {
  description = "ID del workspace Log Analytics (per log alert)"
  type        = string
  default     = null
}

variable "log_query" {
  description = "Query KQL (per log alert)"
  type        = string
  default     = null
}

variable "metric_namespace" {
  description = "Namespace della metrica (es. Microsoft.Sql/servers/databases)"
  type        = string
  default     = null
}

variable "metric_name" {
  description = "Nome della metrica (es. dtu_consumption_percent)"
  type        = string
  default     = null
}

variable "aggregation" {
  description = "Aggregazione: Average, Total, Count, Max, Min"
  type        = string
  default     = "Average"
}

variable "operator" {
  description = "Operatore: GreaterThan, LessThan, GreaterOrLessThan"
  type        = string
  default     = "GreaterThan"
}

variable "threshold" {
  description = "Soglia dell'alert"
  type        = number
  default     = 80
}

variable "severity" {
  description = "Severità (0-4)"
  type        = number
  default     = 2
}

variable "frequency" {
  description = "Frequenza di valutazione (ISO 8601, es. PT1M)"
  type        = string
  default     = "PT5M"
}

variable "window_size" {
  description = "Finestra di valutazione (ISO 8601, es. PT15M)"
  type        = string
  default     = "PT15M"
}
