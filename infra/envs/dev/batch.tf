# AWS Batch on Fargate + the unpack-archive job. Uses the default VPC's public
# subnets (tasks need a public IP to reach ECR/S3 — no NAT in this dev setup)
# and the Batch service-linked role (no explicit service role).

resource "aws_ecr_repository" "unpack_archive" {
  name         = "imgp/unpack-archive"
  force_delete = true
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "batch" {
  name        = "${local.prefix}-dev-batch"
  description = "Batch Fargate tasks (egress only)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_batch_compute_environment" "fargate" {
  name = "${local.prefix}-dev-fargate"
  type = "MANAGED"

  compute_resources {
    type               = "FARGATE"
    max_vcpus          = 4
    subnets            = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.batch.id]
  }
}

resource "aws_batch_job_queue" "default" {
  name     = "${local.prefix}-dev-default"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.fargate.arn
  }
}

# --- Job roles ---------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_exec" {
  name               = "${local.prefix}-dev-batch-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "batch_exec" {
  role       = aws_iam_role.batch_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "unpack_job" {
  name               = "${local.prefix}-dev-unpack-job"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "unpack_job" {
  name = "unpack-job"
  role = aws_iam_role.unpack_job.id

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
        Action   = ["dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.ingest_ledger.arn
      },
    ]
  })
}

# --- Job definition ----------------------------------------------------------

resource "aws_batch_job_definition" "unpack_archive" {
  name                  = "${local.prefix}-dev-unpack-archive"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.unpack_archive.repository_url}:latest"
    jobRoleArn       = aws_iam_role.unpack_job.arn
    executionRoleArn = aws_iam_role.batch_exec.arn

    resourceRequirements = [
      { type = "VCPU", value = "1" },
      { type = "MEMORY", value = "2048" },
    ]

    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }

    fargatePlatformConfiguration = {
      platformVersion = "LATEST"
    }

    environment = [
      { name = "BRONZE_BUCKET", value = module.bronze.bucket },
      { name = "LEDGER_TABLE", value = aws_dynamodb_table.ingest_ledger.name },
    ]
  })

  timeout {
    attempt_duration_seconds = 3600
  }
}

output "unpack_archive_ecr" {
  value = aws_ecr_repository.unpack_archive.repository_url
}
