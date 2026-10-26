# -----------------------------------------------------------------------------
# Monthly data-retention purge
#
# backend/scripts/run_retention.py deletes rows past their retention windows
# (backend/services/data_retention.py). It runs as a one-off Fargate task on
# the service's latest task definition with the command overridden, so it
# runs exactly once per month however many tasks or workers the service has,
# and a deploy cannot reset its timer the way it resets the in-process loops
# in backend/main.py. Output lands in /ecs/wizscheduler under the task's id.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "retention" {
  name                = "${var.app_name}-monthly-retention"
  description         = "Run the data-retention purge at 03:00 UTC on the 1st of each month"
  schedule_expression = "cron(0 3 1 * ? *)"

  tags = { Name = "${var.app_name}-monthly-retention" }
}

resource "aws_cloudwatch_event_target" "retention" {
  rule     = aws_cloudwatch_event_rule.retention.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.retention_events.arn

  ecs_target {
    # No revision: run the latest ACTIVE revision, which the deploy job
    # keeps pinned to the image currently serving traffic.
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
  # neither runs migrations nor starts uvicorn.
  input = jsonencode({
    containerOverrides = [{
      name    = var.app_name
      command = ["python", "-m", "backend.scripts.run_retention"]
    }]
  })
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "retention_events" {
  name               = "${var.app_name}-retention-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json

  tags = { Name = "${var.app_name}-retention-events" }
}

data "aws_iam_policy_document" "retention_events" {
  statement {
    sid       = "RunRetentionTask"
    actions   = ["ecs:RunTask"]
    resources = ["${aws_ecs_task_definition.app.arn_without_revision}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }

  statement {
    sid     = "PassTaskRoles"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.ecs_execution.arn,
      aws_iam_role.ecs_task.arn,
    ]
  }
}

resource "aws_iam_role_policy" "retention_events" {
  name   = "${var.app_name}-retention-events"
  role   = aws_iam_role.retention_events.id
  policy = data.aws_iam_policy_document.retention_events.json
}
