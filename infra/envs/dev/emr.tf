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

# The account's EMR Serverless vCPU quota is 0 (increase denied 2026-08-21:
# insufficient usage history; re-request after the next billing cycle). Until
# granted, Spark runs in local mode on Batch/Fargate (see spark_runner below)
# and this application stays disabled.
variable "emr_enabled" {
  type    = bool
  default = false
}

resource "aws_emrserverless_application" "spark" {
  count = var.emr_enabled ? 1 : 0

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
        # ecs-tasks so the same role serves the local-mode Batch runner.
        Service = ["emr-serverless.amazonaws.com", "ecs-tasks.amazonaws.com"]
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

# --- Local-mode Spark runner (interim, until EMR quota) ---------------------

resource "aws_ecr_repository" "spark_runner" {
  name         = "imgp/spark-runner"
  force_delete = true
}

resource "aws_batch_job_definition" "spark_runner" {
  name                  = "${local.prefix}-dev-spark-runner"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.spark_runner.repository_url}:latest"
    jobRoleArn       = aws_iam_role.emr_job.arn
    executionRoleArn = aws_iam_role.batch_exec.arn

    # Entrypoint is `spark-submit --master local[*]`; override command with
    # ["<script>.py", "--arg", ...] per submit.
    command = ["silver_build.py"]

    resourceRequirements = [
      { type = "VCPU", value = "4" },
      { type = "MEMORY", value = "16384" },
    ]

    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }

    fargatePlatformConfiguration = {
      platformVersion = "LATEST"
    }

    environment = [
      { name = "AWS_REGION", value = "us-east-1" },
    ]
  })

  timeout {
    attempt_duration_seconds = 3600
  }
}

output "emr_application_id" {
  value = var.emr_enabled ? aws_emrserverless_application.spark[0].id : "disabled (vCPU quota pending)"
}

output "emr_job_role" {
  value = aws_iam_role.emr_job.arn
}

output "artifacts_bucket" {
  value = module.artifacts.bucket
}
