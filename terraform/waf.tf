# -----------------------------------------------------------------------------
# AWS WAFv2 (#47) — blanket per-IP rate limit at the ALB edge
#
# Stops scripted floods, vulnerability scans, and dumb DoS before they reach
# FastAPI. Sized loose enough that legitimate traffic never hits it; the
# tighter app-layer limits in backend/services/rate_limit.py cover the
# cost-sensitive endpoints. Both layers compose — WAF reduces what the
# app-layer ever has to count.
#
# Pricing: $1/web ACL/month + $1/rule/month + $0.60 per million requests
# evaluated. Negligible at our traffic.
# -----------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "main" {
  name        = "${var.app_name}-waf"
  description = "Blanket per-IP rate limit + visibility for ${var.app_name}"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "blanket-rate-limit-per-ip"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 100 # per 5-minute window per source IP
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.app_name}-waf-blanket-ratelimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.app_name}-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name        = "${var.app_name}-waf"
    Environment = var.environment
  }
}

resource "aws_wafv2_web_acl_association" "main" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
