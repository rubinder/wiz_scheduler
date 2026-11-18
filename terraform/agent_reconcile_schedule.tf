# -----------------------------------------------------------------------------
# Daily AI host billing reconcile (#259 step 5)
#
# backend/scripts/run_agent_overage_reconcile.py re-syncs every agent
# subscription from Stripe, bills last period's usage over the included
# amount, and runs the voice-number daily step (a no-op while
# VOICE_PROVISIONING_ENABLED is off). Same shape as the Stripe reconcile: a
# one-off Fargate task on the service's latest task definition with the
# command overridden, reusing the retention schedule's EventBridge role.
#
# Gated on agent_reconcile_enabled (default false) so merging this is
# plan-neutral; the owner flips it together with RESERVATIONS_ENABLED.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "agent_reconcile" {
  count = var.agent_reconcile_enabled ? 1 : 0

  name                = "${var.app_name}-daily-agent-reconcile"
  description         = "Reconcile AI host subscriptions, bill agent overage and run the voice-number step at 05:00 UTC daily"
  schedule_expression = "cron(0 5 * * ? *)"

  tags = { Name = "${var.app_name}-daily-agent-reconcile" }
}

resource "aws_cloudwatch_event_target" "agent_reconcile" {
  count = var.agent_reconcile_enabled ? 1 : 0

  rule     = aws_cloudwatch_event_rule.agent_reconcile[0].name
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
      command = ["python", "-m", "backend.scripts.run_agent_overage_reconcile"]
    }]
  })
}
