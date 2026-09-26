variable "name" {
  description = "Nome del Synapse workspace (es. syn-ai-dev-we-01)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group di destinazione."
  type        = string
}

variable "location" {
  description = "Regione Azure."
  type        = string
}

variable "datalake_storage_account_name" {
  description = "Nome dello storage account ADLS Gen2 per Synapse (senza trattini)."
  type        = string
}

variable "datalake_filesystem_name" {
  description = "Nome del filesystem ADLS Gen2 (container)."
  type        = string
  default     = "synapse"
}

variable "sql_administrator_login" {
  description = "Login admin SQL del workspace Synapse."
  type        = string
  sensitive   = true
}

variable "sql_administrator_login_password" {
  description = "Password admin SQL del workspace Synapse."
  type        = string
  sensitive   = true
}

variable "managed_virtual_network_enabled" {
  description = "Abilita la Managed Virtual Network del workspace (isolamento rete)."
  type        = bool
  default     = true
}

variable "network_default_action" {
  description = "Azione di default delle network rules dello storage ADLS (Allow/Deny)."
  type        = string
  default     = "Deny"
}

variable "allowed_subnet_ids" {
  description = "Subnet autorizzate ad accedere allo storage ADLS."
  type        = list(string)
  default     = []
}

variable "allowed_ip_addresses" {
  description = "IP pubblici autorizzati ad accedere allo storage ADLS."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tag delle risorse."
  type        = map(string)
  default     = {}
}
