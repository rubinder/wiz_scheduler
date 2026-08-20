terraform {
  # Pin an UPPER bound, not just a floor. Terraform stamps its own version into
  # the remote state and refuses to read state written by anything newer, so an
  # open ">= x" constraint lets a contributor's newer local CLI apply once and
  # lock the CI runner (pinned in .github/workflows/deploy.yml) out of its own
  # state. Keep this range and the workflow's terraform_version in step.
  required_version = "~> 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state — S3 + DynamoDB lock table (resources defined in state.tf).
  # Migrated from local state on 2026-05-13.
  backend "s3" {
    bucket         = "wizscheduler-tfstate"
    key            = "terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "wizscheduler-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
  # Credentials resolved via the standard AWS chain: env vars → shared
  # credentials file → shared config profile (set AWS_PROFILE) → IMDS.
  # No explicit `profile` so CI (env-var creds) and local dev both work.
  default_tags {
    tags = {
      project     = var.app_name
      managed_by  = "terraform"
      environment = var.environment
    }
  }
}
