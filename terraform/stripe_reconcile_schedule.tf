# -----------------------------------------------------------------------------
# Daily Stripe reconciliation
#
# backend/scripts/run_stripe_reconcile.py checks every paid ownership group
# against Stripe and records cancellations a webhook missed, then checks the
# webhook endpoint's URL and events (backend/services/stripe_reconcile.py).
# Like the retention purge it runs as a one-off Fargate task on the service's
# latest task definition with the command overridden, so it runs once a day
# however many tasks or workers the service has, and sends one alert.
# It reuses the retention schedule's EventBridge role, which may run only this
# task definition family in this cluster.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "stripe_reconcile" {
  name                = "${var.app_name}-daily-stripe-reconcile"
  description         = "Reconcile subscriptions and the webhook endpoint with Stripe at 04:00 UTC daily"
  schedule_expression = "cron(0 4 * * ? *)"

  tags = { Name = "${var.app_name}-daily-stripe-reconcile" }
}

resource "aws_cloudwatch_event_target" "stripe_reconcile" {
  rule     = aws_cloudwatch_event_rule.stripe_reconcile.name
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
      command = ["python", "-m", "backend.scripts.run_stripe_reconcile"]
    }]
  })
}
