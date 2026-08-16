# Gold-builder Batch job. Submitted manually for now; event/schedule-driven
# orchestration is a Phase 5 concern.

resource "aws_ecr_repository" "gold_jobs" {
  name         = "imgp/gold-jobs"
  force_delete = true
}

resource "aws_iam_role" "gold_job" {
  name               = "${local.prefix}-dev-gold-job"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "gold_job" {
  name = "gold-job"
  role = aws_iam_role.gold_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSilver"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.silver.arn}/*"
      },
      {
        Sid      = "ListSilver"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = module.silver.arn
      },
      {
        Sid      = "WriteGold"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.gold.arn}/*"
      },
    ]
  })
}

resource "aws_batch_job_definition" "gold_builder" {
  name                  = "${local.prefix}-dev-gold-builder"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.gold_jobs.repository_url}:latest"
    jobRoleArn       = aws_iam_role.gold_job.arn
    executionRoleArn = aws_iam_role.batch_exec.arn

    resourceRequirements = [
      { type = "VCPU", value = "2" },
      { type = "MEMORY", value = "4096" },
    ]

    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }

    fargatePlatformConfiguration = {
      platformVersion = "LATEST"
    }

    environment = [
      { name = "SILVER_BUCKET", value = module.silver.bucket },
      { name = "GOLD_BUCKET", value = module.gold.bucket },
    ]
  })

  timeout {
    attempt_duration_seconds = 3600
  }
}

output "gold_jobs_ecr" {
  value = aws_ecr_repository.gold_jobs.repository_url
}
