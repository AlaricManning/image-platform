# EMR Serverless: the Spark engine for tabular transforms (silver staging ->
# Iceberg tables, gold builds). Glue Data Catalog is the metastore; Glue ETL is
# deliberately not used. Jobs are submitted manually for now (orchestration is
# a later phase):
#
#   aws emr-serverless start-job-run --application-id <id> \
#     --execution-role-arn <imgp-dev-emr-job arn> \
#     --job-driver '{"sparkSubmit": {"entryPoint": "s3://<artifacts>/spark/silver_build.py", ...}}'

module "artifacts" {
  source = "../../modules/data-bucket"

  name          = "${local.prefix}-dev-artifacts-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_object" "spark_scripts" {
  for_each = fileset("${path.module}/../../../jobs/spark", "*.py")

  bucket      = module.artifacts.bucket
  key         = "spark/${each.value}"
  source      = "${path.module}/../../../jobs/spark/${each.value}"
  source_hash = filemd5("${path.module}/../../../jobs/spark/${each.value}")
}

resource "aws_emrserverless_application" "spark" {
  name          = "${local.prefix}-dev-spark"
  release_label = "emr-7.2.0"
  type          = "SPARK"

  auto_start_configuration {
    enabled = true
  }

  # Cost guardrail: the app fully stops (no idle billing) after 15 idle minutes.
  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 15
  }

  maximum_capacity {
    cpu    = "8 vCPU"
    memory = "32 GB"
  }
}

resource "aws_iam_role" "emr_job" {
  name = "${local.prefix}-dev-emr-job"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "emr-serverless.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "emr_job" {
  name = "emr-job"
  role = aws_iam_role.emr_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LakeReadWrite"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${module.silver.arn}/*",
          "${module.gold.arn}/*",
        ]
      },
      {
        Sid      = "LakeList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [module.silver.arn, module.gold.arn, module.artifacts.arn]
      },
      {
        Sid      = "Scripts"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.artifacts.arn}/spark/*"
      },
      {
        Sid      = "JobLogs"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.artifacts.arn}/emr-logs/*"
      },
      {
        Sid    = "GlueCatalog"
        Effect = "Allow"
        Action = ["glue:*"]
        Resource = [
          "arn:aws:glue:us-east-1:${local.account_id}:catalog",
          "arn:aws:glue:us-east-1:${local.account_id}:database/imgp_dev_silver",
          "arn:aws:glue:us-east-1:${local.account_id}:database/imgp_dev_gold",
          "arn:aws:glue:us-east-1:${local.account_id}:database/default",
          "arn:aws:glue:us-east-1:${local.account_id}:table/imgp_dev_silver/*",
          "arn:aws:glue:us-east-1:${local.account_id}:table/imgp_dev_gold/*",
        ]
      },
    ]
  })
}

output "emr_application_id" {
  value = aws_emrserverless_application.spark.id
}

output "emr_job_role" {
  value = aws_iam_role.emr_job.arn
}

output "artifacts_bucket" {
  value = module.artifacts.bucket
}
