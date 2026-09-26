variable "location" {
  description = "Regione Azure per le risorse (dev)."
  type        = string
  default     = "westeurope"
}

variable "allowed_ip_addresses" {
  description = "ACL esplicite: IP pubblici autorizzati ad accedere a Storage/KeyVault (es. runner CI/CD). Vuoto = solo VNet."
  type        = list(string)
  default     = []
}

variable "sql_admin_login" {
  description = "Login admin SQL Server (da GitHub secret TF_VAR_sql_admin_login)"
  type        = string
  sensitive   = true
}

variable "sql_admin_password" {
  description = "Password admin SQL Server (da GitHub secret TF_VAR_sql_admin_password)"
  type        = string
  sensitive   = true
}

variable "budget_amount" {
  description = "Importo mensile del budget (EUR)."
  type        = number
  default     = 20
}

variable "budget_start_date" {
  description = "Data di inizio del budget (RFC3339, primo del mese)."
  type        = string
  default     = "2026-10-01T00:00:00Z"
}

variable "budget_contact_emails" {
  description = "Email di notifica del budget."
  type        = list(string)
  default     = ["cuffaricristianfelice@gmail.com"]
}

variable "agent_webhook_url" {
  description = "URL HTTPS dell'agente (Cloudflare Tunnel) che riceve i webhook degli alert Azure."
  type        = string
  default     = "https://pairs-tyler-got-enrolled.trycloudflare.com/webhook/azure-alert"
}
