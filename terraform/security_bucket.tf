# -----------------------------------------------------------------------------
# Private bucket for security reports
#
# Created by hand on 2026-10-25 and adopted here via the import blocks below.
# Holds audit write-ups the public repo must not carry; GitHub issues only
# point at objects in it. Nothing in the app reads or writes it.
#
# Encryption is deliberately not managed here: the bucket has AWS's default
# SSE-S3 (bucket key on) with SSE-C blocked, and provider 5.x cannot express
# the SSE-C block, so a managed configuration could drop it. Adopt it once
# the provider supports blocked_encryption_types.
# -----------------------------------------------------------------------------

locals {
  security_bucket = "${var.app_name}-security-${data.aws_caller_identity.current.account_id}"
}

import {
  to = aws_s3_bucket.security
  id = local.security_bucket
}

import {
  to = aws_s3_bucket_versioning.security
  id = local.security_bucket
}

import {
  to = aws_s3_bucket_public_access_block.security
  id = local.security_bucket
}

import {
  to = aws_s3_bucket_ownership_controls.security
  id = local.security_bucket
}

resource "aws_s3_bucket" "security" {
  bucket = local.security_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "security" {
  bucket = aws_s3_bucket.security.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "security" {
  bucket                  = aws_s3_bucket.security.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "security" {
  bucket = aws_s3_bucket.security.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
