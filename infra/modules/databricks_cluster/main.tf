resource "databricks_cluster" "this" {
  cluster_name  = var.cluster_name
  spark_version = var.spark_version
  node_type_id  = var.node_type_id

  num_workers              = var.num_workers
  autotermination_minutes  = var.autotermination_minutes
  data_security_mode       = var.data_security_mode

  # Flexible node types: fallback automatico su SKU compatibili in caso di stockout.
  # Mitiga errori CLOUD_PROVIDER_RESOURCE_STOCKOUT (SkuNotAvailable).
  enable_elastic_disk = true

  custom_tags = var.tags
}
