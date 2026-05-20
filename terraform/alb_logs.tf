# -----------------------------------------------------------------------------
# ALB access logs — S3 bucket + policy
#
# Enables per-request observability (method, path, status, source IP) for the
# ALB. Without this, login 401s and similar failures leave no trace because
# the FastAPI app doesn't emit access logs and CloudWatch only sees what the
# app itself logs.
#
# Logs flow: ALB → s3://wizscheduler-alb-logs-<account>/AWSLogs/<account>/...
# Lifecycle: expire after 90 days.
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_elb_service_account" "main" {}

resource "aws_s3_bucket" "alb_logs" {
  bucket        = "${var.app_name}-alb-logs-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = { Name = "${var.app_name}-alb-logs" }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-after-90-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = data.aws_elb_service_account.main.arn }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.alb_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
      }
    ]
  })
}
