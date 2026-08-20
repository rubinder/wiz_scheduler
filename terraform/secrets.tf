# -----------------------------------------------------------------------------
# Secrets Manager
#
# - DATABASE_URL is fully managed by terraform (the connection string is
#   derived from db_username/db_password vars + the RDS endpoint, so
#   rotating db_password via TF_VAR_db_password updates this secret too).
#
# - SECRET_KEY, ANTHROPIC_API_KEY, RESEND_API_KEY are seeded with
#   placeholders the first time terraform runs, then expected to be
#   set manually via the AWS Console or CLI. The lifecycle
#   ignore_changes block on each version prevents future terraform
#   applies from clobbering the real value.
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.app_name}/${var.environment}/DATABASE_URL"
  description             = "PostgreSQL connection string for WizScheduler"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-database-url" }
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+asyncpg://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}/${var.db_name}"
}

resource "aws_secretsmanager_secret" "secret_key" {
  name                    = "${var.app_name}/${var.environment}/SECRET_KEY"
  description             = "JWT signing secret for WizScheduler"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-secret-key" }
}

resource "aws_secretsmanager_secret_version" "secret_key" {
  secret_id     = aws_secretsmanager_secret.secret_key.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.app_name}/${var.environment}/ANTHROPIC_API_KEY"
  description             = "Anthropic API key for Claude LLM calls"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-anthropic-api-key" }
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  secret_id     = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "resend_api_key" {
  name                    = "${var.app_name}/${var.environment}/RESEND_API_KEY"
  description             = "Resend API key for transactional emails"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-resend-api-key" }
}

resource "aws_secretsmanager_secret_version" "resend_api_key" {
  secret_id     = aws_secretsmanager_secret.resend_api_key.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "demo_seed_password" {
  name                    = "${var.app_name}/${var.environment}/DEMO_SEED_PASSWORD"
  description             = "Login password for the seeded demo manager/employee accounts"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-demo-seed-password" }
}

# NOTE: this secret already exists in the prod account (created out-of-band
# during the credential rotation). Adopt it instead of recreating:
#   terraform import aws_secretsmanager_secret.demo_seed_password \
#     wizscheduler/prod/DEMO_SEED_PASSWORD
resource "aws_secretsmanager_secret_version" "demo_seed_password" {
  secret_id     = aws_secretsmanager_secret.demo_seed_password.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "stripe_secret_key" {
  name                    = "${var.app_name}/${var.environment}/STRIPE_SECRET_KEY"
  description             = "Stripe API secret key for billing checkout sessions"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-stripe-secret-key" }
}

resource "aws_secretsmanager_secret_version" "stripe_secret_key" {
  secret_id     = aws_secretsmanager_secret.stripe_secret_key.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "stripe_webhook_secret" {
  name                    = "${var.app_name}/${var.environment}/STRIPE_WEBHOOK_SECRET"
  description             = "Stripe webhook signing secret (whsec_...)"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-stripe-webhook-secret" }
}

resource "aws_secretsmanager_secret_version" "stripe_webhook_secret" {
  secret_id     = aws_secretsmanager_secret.stripe_webhook_secret.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}
