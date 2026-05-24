# -----------------------------------------------------------------------------
# CloudWatch alarms (#49) — first two of four planned
#
# Scope of this file: alarms that read AWS-native metrics directly, no
# custom-metric emission needed.
#
#   (C) ALB 4XX surge       — HTTPCode_Target_4XX_Count from the ALB
#   (D) ECS Fargate CPU     — CPUUtilization from the ECS service
#
# Deferred to a follow-up (need a Lambda or app-side put_metric_data):
#   (A) Resend daily-send surge      — no native metric exists for Resend
#   (B) Per-OG Anthropic spend > $50 — would need to query TokenUsageDaily
#                                      and publish a custom metric per OG
#
# Notification path: single SNS topic, email-subscribed via var.ops_alert_email.
# Subscription confirmation happens out-of-band: SNS emails the address and
# the recipient must click the link. Don't bother adding PagerDuty until
# we're paging more than once per quarter.
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "ops_alerts" {
  name = "${var.app_name}-ops-alerts"

  tags = {
    Name        = "${var.app_name}-ops-alerts"
    Environment = var.environment
  }
}

# Only subscribe an email when one is configured — keeps `terraform apply`
# usable in environments (dev, ephemeral) that don't have an ops inbox yet.
resource "aws_sns_topic_subscription" "ops_alerts_email" {
  count     = var.ops_alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.ops_alert_email
}

# -----------------------------------------------------------------------------
# (C) ALB 4XX surge
#
# Catches: ongoing rate-limit triggers, scraper attacks, a frontend
# regression flooding the API with malformed requests.
#
# Static threshold to start (sustained 100/min for 5 of 5 evaluation
# periods). Once we have 30 days of CloudWatch data we can swap to an
# anomaly detector — for now a known-bad number beats unfounded
# percentiles.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "alb_4xx_surge" {
  alarm_name          = "${var.app_name}-alb-4xx-surge"
  alarm_description   = "ALB target 4XX responses > 100/min for 5 consecutive minutes"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_4XX_Count"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 100
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = {
    Name        = "${var.app_name}-alb-4xx-surge"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# (D) ECS Fargate CPU sustained > 80%
#
# Catches: bcrypt brute force pinning a worker, runaway LLM call, or
# real load that should trigger autoscaling (when we add it). 80% for 5
# consecutive minutes — a healthy spike will not sustain that long.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  alarm_name          = "${var.app_name}-ecs-cpu-high"
  alarm_description   = "ECS service CPU > 80% sustained 5 minutes"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.app.name
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = {
    Name        = "${var.app_name}-ecs-cpu-high"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# WAF block-rate visibility (companion to waf.tf)
#
# WAF emits BlockedRequests as a CloudWatch metric automatically once the
# Web ACL's visibility_config.cloudwatch_metrics_enabled = true. This alarm
# fires on a sustained block rate so we can spot a scripted flood that the
# rate-based rule is shedding — both confirms the WAF is working AND tells
# us when something interesting is happening at the edge.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "waf_blocks_high" {
  alarm_name          = "${var.app_name}-waf-blocks-high"
  alarm_description   = "WAF blocking > 50 requests/min for 5 consecutive minutes (scripted abuse)"
  namespace           = "AWS/WAFV2"
  metric_name         = "BlockedRequests"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 50
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = aws_wafv2_web_acl.main.name
    Region = var.aws_region
    Rule   = "ALL"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = {
    Name        = "${var.app_name}-waf-blocks-high"
    Environment = var.environment
  }
}
