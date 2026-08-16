# One-time bootstrap: creates the remote-state bucket and the project's deploy
# IAM user. Runs under the admin `default` profile with local state (gitignored);
# everything else in infra/ runs as the `image-platform` profile created here.
#
# Convention: every IAM role/policy this project creates is named `imgp-*` —
# that prefix is what the deploy user's IAM permissions are scoped to.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region  = "us-east-1"
  profile = "default"

  default_tags {
    tags = {
      Project   = "image-platform"
      ManagedBy = "terraform"
      Component = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

# --- Remote state bucket -----------------------------------------------------

resource "aws_s3_bucket" "tfstate" {
  bucket = "imgp-tfstate-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Deploy identity ---------------------------------------------------------

resource "aws_iam_user" "deploy" {
  name = "image-platform-deploy"
}

resource "aws_iam_user_policy_attachment" "deploy_poweruser" {
  user       = aws_iam_user.deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# PowerUserAccess excludes IAM writes; grant them only for imgp-* entities so
# the deploy user can manage this project's roles but nothing else's.
resource "aws_iam_policy" "deploy_iam" {
  name = "imgp-deploy-iam"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageProjectIam"
        Effect = "Allow"
        Action = "iam:*"
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/imgp-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/imgp-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:instance-profile/imgp-*",
        ]
      }
    ]
  })
}

resource "aws_iam_user_policy_attachment" "deploy_iam" {
  user       = aws_iam_user.deploy.name
  policy_arn = aws_iam_policy.deploy_iam.arn
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "deploy_user" {
  value = aws_iam_user.deploy.name
}
