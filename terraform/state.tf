# -----------------------------------------------------------------------------
# Remote State Infrastructure (S3 + DynamoDB)
# These resources must exist BEFORE enabling the S3 backend in main.tf.
# Run `terraform/bootstrap-state.sh` once to create them, then uncomment the
# backend block in main.tf and run `terraform init -migrate-state`.
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "tfstate" {
  bucket = "${var.app_name}-tfstate"

  lifecycle {
    prevent_destroy = true
  }

  tags = { Name = "${var.app_name}-tfstate" }
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
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tflock" {
  name         = "${var.app_name}-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = { Name = "${var.app_name}-tflock" }
}
