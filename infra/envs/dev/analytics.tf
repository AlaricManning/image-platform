# Query layer: Glue Data Catalog databases (metastore only — table metadata is
# owned by Iceberg writers, so no aws_glue_catalog_table resources and no
# partition projection) + an Athena workgroup. Athena reads Iceberg natively
# from the catalog.

resource "aws_glue_catalog_database" "silver" {
  name         = "imgp_dev_silver"
  location_uri = "s3://${module.silver.bucket}/warehouse/"
}

resource "aws_glue_catalog_database" "gold" {
  name         = "imgp_dev_gold"
  location_uri = "s3://${module.gold.bucket}/warehouse/"
}

module "athena_results" {
  source = "../../modules/data-bucket"

  name          = "${local.prefix}-dev-athena-results-${local.account_id}"
  expire_days   = 30
  force_destroy = true
}

resource "aws_athena_workgroup" "dev" {
  name          = "${local.prefix}-dev"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration = true
    # Cost guardrail: fail any query that would scan more than 1 GB.
    bytes_scanned_cutoff_per_query = 1073741824

    result_configuration {
      output_location = "s3://${module.athena_results.bucket}/"
    }
  }
}

output "athena_workgroup" {
  value = aws_athena_workgroup.dev.name
}
