"""Reservations decision 28 (Ruling O), phase 1: CloudFront sends the origin
secret to the ALB; the backend does not enforce it yet."""

import re
from pathlib import Path

TF = Path(__file__).resolve().parents[2] / "terraform"


def test_cloudfront_sends_origin_header_from_the_secret():
    text = (TF / "frontend.tf").read_text()
    origin = re.search(r'origin_id\s+= "alb-api"(.*?)\n  \}', text, re.S).group(1)
    assert 'name  = "X-Origin-Verify"' in origin
    assert "aws_secretsmanager_secret_version.public_origin_secret.secret_string" in origin


def test_origin_secret_is_generated_and_never_overwritten():
    text = (TF / "secrets.tf").read_text()
    version = re.search(
        r'resource "aws_secretsmanager_secret_version" "public_origin_secret" \{(.*?)\n\}',
        text, re.S).group(1)
    assert "random_password" in version
    assert "ignore_changes = [secret_string]" in version


def test_extra_alert_email_is_sensitive_and_from_a_secret():
    variables = (TF / "variables.tf").read_text()
    var = re.search(r'variable "ops_alert_extra_email" \{(.*?)\n\}', variables, re.S).group(1)
    assert "sensitive   = true" in var
    assert 'default     = ""' in var
    deploy = (TF.parent / ".github" / "workflows" / "deploy.yml").read_text()
    assert deploy.count("TF_VAR_ops_alert_extra_email: ${{ secrets.OPS_ALERT_EXTRA_EMAIL }}") == 2
    assert "@gmail.com" not in deploy
