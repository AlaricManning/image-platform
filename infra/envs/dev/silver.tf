# Bronze -> silver processing: EventBridge fires the silver-router Lambda when
# a bronze _manifest.json lands; the router submits image-profile and/or
# normalize-annotations Batch jobs (one shared container image, command set per
# job definition). Reuses the Phase 2 Fargate compute environment and queue.

resource "aws_ecr_repository" "silver_jobs" {
  name         = "imgp/silver-jobs"
  force_delete = true
}

resource "aws_iam_role" "silver_job" {
  name               = "${local.prefix}-dev-silver-job"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "silver_job" {
  name = "silver-job"
  role = aws_iam_role.silver_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadBronze"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.bronze.arn}/*"
      },
      {
        Sid      = "WriteSilver"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.silver.arn}/*"
      },
    ]
  })
}

locals {
  silver_job_base = {
    jobRoleArn       = aws_iam_role.silver_job.arn
    executionRoleArn = aws_iam_role.batch_exec.arn

    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }

    fargatePlatformConfiguration = {
      platformVersion = "LATEST"
    }

    environment = [
      { name = "SILVER_BUCKET", value = module.silver.bucket },
    ]
  }
}

resource "aws_batch_job_definition" "image_profile" {
  name                  = "${local.prefix}-dev-image-profile"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode(merge(local.silver_job_base, {
    image   = "${aws_ecr_repository.silver_jobs.repository_url}:latest"
    command = ["python", "image_profile.py"]

    resourceRequirements = [
      { type = "VCPU", value = "2" },
      { type = "MEMORY", value = "4096" },
    ]
  }))

  timeout {
    attempt_duration_seconds = 7200
  }
}

resource "aws_batch_job_definition" "normalize_annotations" {
  name                  = "${local.prefix}-dev-normalize-annotations"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode(merge(local.silver_job_base, {
    image   = "${aws_ecr_repository.silver_jobs.repository_url}:latest"
    command = ["python", "normalize_annotations.py"]

    # instances_train2017.json is a 448 MB JSON document; json.load peaks well
    # above the file size, hence the 8 GB task.
    resourceRequirements = [
      { type = "VCPU", value = "2" },
      { type = "MEMORY", value = "8192" },
    ]
  }))

  timeout {
    attempt_duration_seconds = 3600
  }
}

# --- Silver router Lambda ----------------------------------------------------

resource "aws_iam_role" "silver_router" {
  name               = "${local.prefix}-dev-silver-router"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "silver_router_logs" {
  role       = aws_iam_role.silver_router.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "silver_router" {
  name = "silver-router"
  role = aws_iam_role.silver_router.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadBronzeManifests"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.bronze.arn}/*"
      },
      {
        Sid      = "Ledger"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.ingest_ledger.arn
      },
      {
        Sid    = "SubmitSilverJobs"
        Effect = "Allow"
        Action = ["batch:SubmitJob"]
        Resource = [
          aws_batch_job_queue.default.arn,
          aws_batch_job_definition.image_profile.arn,
          aws_batch_job_definition.normalize_annotations.arn,
        ]
      },
    ]
  })
}

data "archive_file" "silver_router" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/silver_router"
  output_path = "${path.module}/.terraform/build/silver_router.zip"
}

resource "aws_lambda_function" "silver_router" {
  function_name    = "${local.prefix}-dev-silver-router"
  role             = aws_iam_role.silver_router.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.silver_router.output_path
  source_code_hash = data.archive_file.silver_router.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      JOB_QUEUE            = aws_batch_job_queue.default.arn
      IMAGE_PROFILE_JOBDEF = aws_batch_job_definition.image_profile.arn
      NORMALIZE_JOBDEF     = aws_batch_job_definition.normalize_annotations.arn
      LEDGER_TABLE         = aws_dynamodb_table.ingest_ledger.name
    }
  }
}

resource "aws_cloudwatch_event_rule" "bronze_manifest_created" {
  name        = "${local.prefix}-dev-bronze-manifest-created"
  description = "Bronze ingestion batch completed (manifest written)"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [module.bronze.bucket]
      }
      object = {
        key = [{ suffix = "_manifest.json" }]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "bronze_to_silver_router" {
  rule = aws_cloudwatch_event_rule.bronze_manifest_created.name
  arn  = aws_lambda_function.silver_router.arn
}

resource "aws_lambda_permission" "eventbridge_invoke_silver_router" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.silver_router.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.bronze_manifest_created.arn
}

output "silver_jobs_ecr" {
  value = aws_ecr_repository.silver_jobs.repository_url
}
