terraform {
  required_version = ">= 1.5.0"

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
