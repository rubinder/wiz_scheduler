terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state — uncomment AFTER running `terraform apply` once so the S3
  # bucket and DynamoDB table from state.tf exist, then run:
  #   terraform init -migrate-state
  #
  # backend "s3" {
  #   bucket         = "wizscheduler-tfstate"
  #   key            = "terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "wizscheduler-tflock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project     = var.app_name
      managed_by  = "terraform"
      environment = var.environment
    }
  }
}
