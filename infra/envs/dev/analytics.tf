# Query layer: Glue Data Catalog tables over silver/gold parquet + an Athena
# workgroup. Tables use partition projection (no crawlers, no MSCK) — the
# partner/dataset enum values below must grow as new partners/datasets arrive.

locals {
  projection_base = {
    "projection.enabled"            = "true"
    "projection.partner.type"       = "enum"
    "projection.partner.values"     = "coco"
    "projection.ingest_date.type"   = "date"
    "projection.ingest_date.range"  = "2026-08-01,NOW"
    "projection.ingest_date.format" = "yyyy-MM-dd"
  }

  split_projection = {
    "projection.split.type"   = "enum"
    "projection.split.values" = "train2017,val2017"
  }

  # table -> { db, bucket, dataset enum values, split partition?, columns }
  glue_tables = {
    image_metadata = {
      db       = "silver"
      datasets = "val2017"
      split    = false
      columns = [
        { name = "bronze_key", type = "string" },
        { name = "file_name", type = "string" },
        { name = "size_bytes", type = "bigint" },
        { name = "sha256", type = "string" },
        { name = "width", type = "bigint" },
        { name = "height", type = "bigint" },
        { name = "format", type = "string" },
        { name = "mode", type = "string" },
        { name = "exif_make", type = "string" },
        { name = "exif_model", type = "string" },
        { name = "exif_orientation", type = "bigint" },
        { name = "thumbnail_key", type = "string" },
        { name = "error", type = "string" },
      ]
    }
    coco_images = {
      db       = "silver"
      datasets = "annotations_trainval2017"
      split    = true
      columns = [
        { name = "image_id", type = "bigint" },
        { name = "file_name", type = "string" },
        { name = "width", type = "bigint" },
        { name = "height", type = "bigint" },
      ]
    }
    coco_categories = {
      db       = "silver"
      datasets = "annotations_trainval2017"
      split    = true
      columns = [
        { name = "category_id", type = "bigint" },
        { name = "name", type = "string" },
        { name = "supercategory", type = "string" },
      ]
    }
    coco_annotations = {
      db       = "silver"
      datasets = "annotations_trainval2017"
      split    = true
      columns = [
        { name = "annotation_id", type = "bigint" },
        { name = "image_id", type = "bigint" },
        { name = "category_id", type = "bigint" },
        { name = "bbox_x", type = "double" },
        { name = "bbox_y", type = "double" },
        { name = "bbox_w", type = "double" },
        { name = "bbox_h", type = "double" },
        { name = "area", type = "double" },
        { name = "iscrowd", type = "bigint" },
      ]
    }
    coco_captions = {
      db       = "silver"
      datasets = "annotations_trainval2017"
      split    = true
      columns = [
        { name = "caption_id", type = "bigint" },
        { name = "image_id", type = "bigint" },
        { name = "caption", type = "string" },
      ]
    }
    category_stats = {
      db       = "gold"
      datasets = "val2017"
      split    = false
      columns = [
        { name = "category_id", type = "bigint" },
        { name = "category", type = "string" },
        { name = "supercategory", type = "string" },
        { name = "n_annotations", type = "bigint" },
        { name = "n_images", type = "bigint" },
        { name = "avg_area", type = "double" },
        { name = "crowd_annotations", type = "bigint" },
      ]
    }
    ml_manifest = {
      db       = "gold"
      datasets = "val2017"
      split    = false
      columns = [
        { name = "image_id", type = "bigint" },
        { name = "file_name", type = "string" },
        { name = "width", type = "bigint" },
        { name = "height", type = "bigint" },
        { name = "bronze_key", type = "string" },
        { name = "thumbnail_key", type = "string" },
        { name = "size_bytes", type = "bigint" },
        { name = "sha256", type = "string" },
        { name = "n_annotations", type = "bigint" },
        { name = "n_categories", type = "bigint" },
        { name = "categories", type = "string" },
      ]
    }
  }

  glue_db_names = {
    silver = aws_glue_catalog_database.silver.name
    gold   = aws_glue_catalog_database.gold.name
  }

  glue_buckets = {
    silver = module.silver.bucket
    gold   = module.gold.bucket
  }
}

resource "aws_glue_catalog_database" "silver" {
  name = "imgp_dev_silver"
}

resource "aws_glue_catalog_database" "gold" {
  name = "imgp_dev_gold"
}

resource "aws_glue_catalog_table" "tables" {
  for_each = local.glue_tables

  name          = each.key
  database_name = local.glue_db_names[each.value.db]
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(
    { "classification" = "parquet" },
    local.projection_base,
    { "projection.dataset.values" = each.value.datasets, "projection.dataset.type" = "enum" },
    each.value.split ? local.split_projection : {},
  )

  dynamic "partition_keys" {
    for_each = concat(
      [{ name = "partner" }, { name = "dataset" }, { name = "ingest_date" }],
      each.value.split ? [{ name = "split" }] : [],
    )

    content {
      name = partition_keys.value.name
      type = "string"
    }
  }

  storage_descriptor {
    location      = "s3://${local.glue_buckets[each.value.db]}/tables/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = each.value.columns

      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

# --- Athena ------------------------------------------------------------------

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
