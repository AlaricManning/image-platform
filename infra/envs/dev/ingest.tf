# Event-driven ingestion: landing-bucket object events -> EventBridge -> router
# Lambda, which ledgers the drop in DynamoDB and either copies loose objects to
# bronze or submits the unpack-archive Batch job.

resource "aws_dynamodb_table" "ingest_ledger" {
  name         = "${local.prefix}-dev-ingest-ledger"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "drop_id"

  attribute {
    name = "drop_id"
    type = "S"
  }
}

# --- Router Lambda -----------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest_router" {
  name               = "${local.prefix}-dev-ingest-router"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "ingest_router_logs" {
  role       = aws_iam_role.ingest_router.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "ingest_router" {
  name = "ingest-router"
  role = aws_iam_role.ingest_router.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadLanding"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${local.prefix}-dev-landing-*/*"
      },
      {
        Sid      = "WriteBronze"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.bronze.arn}/*"
      },
      {
        Sid      = "Ledger"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.ingest_ledger.arn
      },
      {
        Sid    = "SubmitUnpack"
        Effect = "Allow"
        Action = ["batch:SubmitJob"]
        Resource = [
          aws_batch_job_queue.default.arn,
          aws_batch_job_definition.unpack_archive.arn,
        ]
      },
    ]
  })
}

data "archive_file" "ingest_router" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/ingest_router"
  output_path = "${path.module}/.terraform/build/ingest_router.zip"
}

resource "aws_lambda_function" "ingest_router" {
  function_name    = "${local.prefix}-dev-ingest-router"
  role             = aws_iam_role.ingest_router.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.ingest_router.output_path
  source_code_hash = data.archive_file.ingest_router.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      BRONZE_BUCKET  = module.bronze.bucket
      LEDGER_TABLE   = aws_dynamodb_table.ingest_ledger.name
      JOB_QUEUE      = aws_batch_job_queue.default.arn
      JOB_DEFINITION = aws_batch_job_definition.unpack_archive.arn
    }
  }
}

# --- EventBridge wiring ------------------------------------------------------

resource "aws_cloudwatch_event_rule" "landing_object_created" {
  name        = "${local.prefix}-dev-landing-object-created"
  description = "Object created in any landing bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [module.landing_coco.bucket]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "landing_to_router" {
  rule = aws_cloudwatch_event_rule.landing_object_created.name
  arn  = aws_lambda_function.ingest_router.arn
}

resource "aws_lambda_permission" "eventbridge_invoke_router" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_router.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.landing_object_created.arn
}
