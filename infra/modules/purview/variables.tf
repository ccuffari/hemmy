variable "name" {
  description = "Nome dell'account Purview."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group in cui creare l'account Purview."
  type        = string
}

variable "location" {
  description = "Region Azure."
  type        = string
}

variable "tags" {
  description = "Tag da applicare alle risorse."
  type        = map(string)
  default     = {}
}

variable "role_assignments" {
  description = "Mappa di role assignment da assegnare alla Managed Identity di Purview (chiave -> {scope, role})."
  type = map(object({
    scope = string
    role  = string
  }))
  default = {}
}
