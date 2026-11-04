# -----------------------------------------------------------------------------
# Private bucket for paid payroll archives (#218)
#
# When a cancelled account has bought the 7-year archive, the cancellation
# lifecycle writes one gzipped history CSV here just before the account is
# deleted. The app may write and read archive objects, never delete them;
# each object expires 7 years after it was written. The app uploads with the
# GLACIER_IR storage class, so no transition rule is needed.
#
# Encryption is left at AWS's default SSE-S3 for the same reason as the
# security bucket: provider 5.x cannot express the SSE-C block, so a managed
# encryption configuration could drop it.
# -----------------------------------------------------------------------------

locals {
  payroll_archive_bucket = "${var.app_name}-payroll-archive-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "payroll_archive" {
  bucket = local.payroll_archive_bucket

  lifecycle {
    prevent_destroy = true
  }

  tags = { Name = local.payroll_archive_bucket }
}

resource "aws_s3_bucket_public_access_block" "payroll_archive" {
  bucket                  = aws_s3_bucket.payroll_archive.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "payroll_archive" {
  bucket = aws_s3_bucket.payroll_archive.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "payroll_archive" {
  bucket = aws_s3_bucket.payroll_archive.id

  rule {
    id     = "expire-after-7-years"
    status = "Enabled"
    filter {
      prefix = "archives/"
    }
    expiration {
      days = 2557
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "payroll_archive_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.payroll_archive.arn,
      "${aws_s3_bucket.payroll_archive.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "payroll_archive" {
  bucket = aws_s3_bucket.payroll_archive.id
  policy = data.aws_iam_policy_document.payroll_archive_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.payroll_archive]
}

# The running container writes archives and presigns downloads. No delete.
data "aws_iam_policy_document" "ecs_task_payroll_archive" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.payroll_archive.arn}/archives/*"]
  }
}

resource "aws_iam_role_policy" "ecs_task_payroll_archive" {
  name   = "${var.app_name}-payroll-archive"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_payroll_archive.json
}
