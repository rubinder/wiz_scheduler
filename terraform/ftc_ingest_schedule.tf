# -----------------------------------------------------------------------------
# Weekly FTC Do Not Call reported-numbers ingest (#259 step 7)
#
# backend/scripts/run_ftc_ingest.py fetches the FTC complaint files not yet
# ingested into spam_numbers (call screening) and prunes rows older than
# SPAM_WINDOW_DAYS. Same shape as the agent reconcile: a one-off Fargate
# task on the service's latest task definition with the command overridden,
# reusing the retention schedule's EventBridge role.
#
# Gated on ftc_ingest_enabled (default false) so merging this is
# plan-neutral. The default cadence, Monday 18:00 UTC, runs after the FTC's
# noon Eastern publish.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "ftc_ingest" {
  count = var.ftc_ingest_enabled ? 1 : 0

  name                = "${var.app_name}-weekly-ftc-ingest"
  description         = "Ingest the FTC Do Not Call reported-numbers files for call screening"
  schedule_expression = var.ftc_ingest_schedule

  tags = { Name = "${var.app_name}-weekly-ftc-ingest" }
}

resource "aws_cloudwatch_event_target" "ftc_ingest" {
  count = var.ftc_ingest_enabled ? 1 : 0

  rule     = aws_cloudwatch_event_rule.ftc_ingest[0].name
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
  # neither runs migrations nor starts uvicorn.
  input = jsonencode({
    containerOverrides = [{
      name    = var.app_name
      command = ["python", "-m", "backend.scripts.run_ftc_ingest"]
    }]
  })
}
