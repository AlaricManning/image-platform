# Dev environment. Runs as the `image-platform` profile (deploy user created by
# infra/bootstrap). Backend blocks can't interpolate, so the account id in the
# bucket name is hardcoded.

terraform {
  required_version = ">= 1.10"

  backend "s3" {
    bucket       = "imgp-tfstate-935961368629"
    key          = "envs/dev/terraform.tfstate"
    region       = "us-east-1"
    profile      = "image-platform"
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region  = "us-east-1"
  profile = "image-platform"

  default_tags {
    tags = {
      Project     = "image-platform"
      ManagedBy   = "terraform"
      Environment = "dev"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  prefix     = "imgp"
  account_id = data.aws_caller_identity.current.account_id
}

output "deploy_identity" {
  value = data.aws_caller_identity.current.arn
}
