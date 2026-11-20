# -----------------------------------------------------------------------------
# AI host analytics lake (#259 step 10)
#
# backend/scripts/run_agent_analytics.py lands one Parquet partition per table
# per UTC day at s3://<lake>/lake/<table>/dt=YYYY-MM-DD/part-0.parquet. Glue
# tables (partition projection on dt, so no crawler and no MSCK) let Athena
# read them through the analytics workgroup; the SQL models in analytics/sql
# are applied by hand (analytics/README.md), never by Terraform.
#
# Every resource here is gated on analytics_enabled (default false), so with
# default vars the plan is empty, and ecs.tf only adds ANALYTICS_BUCKET to
# the task definition when it is on.
#
# Columns come from backend/analytics/schemas.py through the generated
# analytics_columns.tf.json (local.analytics_columns); regenerate it with
# `python -m backend.analytics.glue_columns > terraform/analytics_columns.tf.json`.
#
# Encryption is AWS's default SSE-S3 on both buckets, left unmanaged for the
# same reason as security_bucket.tf: provider 5.x cannot express the SSE-C
# block, so a managed configuration could drop it.
# -----------------------------------------------------------------------------

locals {
  analytics_lake_bucket    = "${var.app_name}-analytics-lake-${data.aws_caller_identity.current.account_id}"
  analytics_results_bucket = "${var.app_name}-athena-results-${data.aws_caller_identity.current.account_id}"
  analytics_prefix         = "lake"
  analytics_database       = replace("${var.app_name}_analytics", "-", "_")
}

# --- Lake bucket ---

resource "aws_s3_bucket" "analytics_lake" {
  count = var.analytics_enabled ? 1 : 0

  bucket = local.analytics_lake_bucket

  tags = { Name = local.analytics_lake_bucket }
}

resource "aws_s3_bucket_public_access_block" "analytics_lake" {
  count = var.analytics_enabled ? 1 : 0

  bucket                  = aws_s3_bucket.analytics_lake[0].id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "analytics_lake" {
  count = var.analytics_enabled ? 1 : 0

  bucket = aws_s3_bucket.analytics_lake[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "analytics_lake" {
  count = var.analytics_enabled ? 1 : 0

  bucket = aws_s3_bucket.analytics_lake[0].id

  rule {
    id     = "expire-lake"
    status = "Enabled"
    filter {}
    expiration {
      days = var.analytics_retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "analytics_lake_tls_only" {
  count = var.analytics_enabled ? 1 : 0

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.analytics_lake[0].arn,
      "${aws_s3_bucket.analytics_lake[0].arn}/*",
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

resource "aws_s3_bucket_policy" "analytics_lake" {
  count = var.analytics_enabled ? 1 : 0

  bucket = aws_s3_bucket.analytics_lake[0].id
  policy = data.aws_iam_policy_document.analytics_lake_tls_only[0].json

  depends_on = [aws_s3_bucket_public_access_block.analytics_lake]
}

# --- Athena results bucket ---

resource "aws_s3_bucket" "analytics_results" {
  count = var.analytics_enabled ? 1 : 0

  bucket = local.analytics_results_bucket

  tags = { Name = local.analytics_results_bucket }
}

resource "aws_s3_bucket_public_access_block" "analytics_results" {
  count = var.analytics_enabled ? 1 : 0

  bucket                  = aws_s3_bucket.analytics_results[0].id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "analytics_results" {
  count = var.analytics_enabled ? 1 : 0

  bucket = aws_s3_bucket.analytics_results[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "analytics_results" {
  count = var.analytics_enabled ? 1 : 0

  bucket = aws_s3_bucket.analytics_results[0].id

  rule {
    id     = "expire-results-30-days"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "analytics_results_tls_only" {
  count = var.analytics_enabled ? 1 : 0

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.analytics_results[0].arn,
      "${aws_s3_bucket.analytics_results[0].arn}/*",
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

resource "aws_s3_bucket_policy" "analytics_results" {
  count = var.analytics_enabled ? 1 : 0

  bucket = aws_s3_bucket.analytics_results[0].id
  policy = data.aws_iam_policy_document.analytics_results_tls_only[0].json

  depends_on = [aws_s3_bucket_public_access_block.analytics_results]
}

# --- Glue catalog ---

resource "aws_glue_catalog_database" "analytics" {
  count = var.analytics_enabled ? 1 : 0

  name        = local.analytics_database
  description = "AI host analytics lake (#259 step 10). Views: analytics/sql."
}

resource "aws_glue_catalog_table" "analytics" {
  for_each = var.analytics_enabled ? local.analytics_columns : {}

  name          = each.key
  database_name = aws_glue_catalog_database.analytics[0].name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                      = "TRUE"
    "classification"              = "parquet"
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "2026-11-01,NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "storage.location.template"   = "s3://${aws_s3_bucket.analytics_lake[0].id}/${local.analytics_prefix}/${each.key}/dt=$${dt}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.analytics_lake[0].id}/${local.analytics_prefix}/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

# --- Athena workgroup ---

resource "aws_athena_workgroup" "analytics" {
  count = var.analytics_enabled ? 1 : 0

  name        = "${var.app_name}-analytics"
  description = "AI host analytics (#259 step 10); results expire after 30 days."

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    # 1 GiB per query: a guard against an unpartitioned scan, far above what
    # a day or a month of the lake holds.
    bytes_scanned_cutoff_per_query = 1073741824

    result_configuration {
      output_location = "s3://${aws_s3_bucket.analytics_results[0].id}/results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  tags = { Name = "${var.app_name}-analytics" }
}

# --- IAM: the nightly job (ECS task role), lake prefix only ---

data "aws_iam_policy_document" "ecs_task_analytics" {
  count = var.analytics_enabled ? 1 : 0

  # lake.S3Sink: put_object, head_object (s3:GetObject), get_object on a size
  # mismatch, delete_objects of stale part files.
  statement {
    sid       = "LakeObjects"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.analytics_lake[0].arn}/${local.analytics_prefix}/*"]
  }

  statement {
    sid       = "LakeList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.analytics_lake[0].arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.analytics_prefix}/*"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_task_analytics" {
  count = var.analytics_enabled ? 1 : 0

  name   = "${var.app_name}-analytics-lake"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_analytics[0].json
}

# --- IAM: the analyst (attach to the owner's IAM user; terraform/README.md) ---

data "aws_iam_policy_document" "analytics_reader" {
  count = var.analytics_enabled ? 1 : 0

  statement {
    sid = "AthenaWorkgroup"
    actions = [
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
      "athena:ListQueryExecutions",
      "athena:BatchGetQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = [aws_athena_workgroup.analytics[0].arn]
  }

  statement {
    sid       = "AthenaList"
    actions   = ["athena:ListWorkGroups", "athena:ListDataCatalogs", "athena:ListDatabases"]
    resources = ["*"]
  }

  # Get* reads the catalog; the view DDL in analytics/sql needs table
  # create/update/delete in this database only.
  statement {
    sid = "GlueCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
    ]
    resources = [
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.analytics[0].arn,
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.analytics_database}/*",
    ]
  }

  statement {
    sid       = "LakeRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.analytics_lake[0].arn}/${local.analytics_prefix}/*"]
  }

  statement {
    sid       = "LakeList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.analytics_lake[0].arn]
  }

  statement {
    sid = "Results"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.analytics_results[0].arn}/*"]
  }

  statement {
    sid       = "ResultsList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.analytics_results[0].arn]
  }
}

resource "aws_iam_policy" "analytics_reader" {
  count = var.analytics_enabled ? 1 : 0

  name        = "${var.app_name}-analytics-reader"
  description = "Query the AI host analytics lake through the Athena analytics workgroup."
  policy      = data.aws_iam_policy_document.analytics_reader[0].json
}

# --- Nightly schedule (same shape as agent_reconcile_schedule.tf) ---

resource "aws_cloudwatch_event_rule" "analytics" {
  count = var.analytics_enabled ? 1 : 0

  name                = "${var.app_name}-nightly-agent-analytics"
  description         = "Land the AI host analytics lake and daily metrics for D-1 and D-2"
  schedule_expression = var.analytics_schedule

  tags = { Name = "${var.app_name}-nightly-agent-analytics" }
}

resource "aws_cloudwatch_event_target" "analytics" {
  count = var.analytics_enabled ? 1 : 0

  rule     = aws_cloudwatch_event_rule.analytics[0].name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.retention_events.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.app.arn_without_revision
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = aws_subnet.private[*].id
      security_groups  = [aws_security_group.ecs.id]
      assign_public_ip = false
    }
  }

  # Overriding command (not entryPoint) skips the image's CMD, so this task
  # neither runs migrations nor starts uvicorn. No args: D-1 and D-2 (UTC).
  input = jsonencode({
    containerOverrides = [{
      name    = var.app_name
      command = ["python", "-m", "backend.scripts.run_agent_analytics"]
    }]
  })
}
