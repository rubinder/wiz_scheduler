variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "app_name" {
  description = "Application name used for resource naming and tagging"
  type        = string
  default     = "wizscheduler"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to use"
  type        = number
  default     = 2
}

# --- ECS / Container ---

variable "container_port" {
  description = "Port the application container listens on"
  type        = number
  default     = 8000
}

variable "container_cpu" {
  description = "CPU units for the Fargate task (1024 = 1 vCPU)"
  type        = number
  default     = 512
}

variable "container_memory" {
  description = <<-DESC
    Memory (MiB) for the Fargate task.

    Raised 1024 -> 2048 on 2026-08-21. At 1024 the service ran at 92% memory
    average and 98.4% peak with CPU at 1.6% — memory-bound at essentially zero
    traffic, one spike from an OOM kill. The footprint is the 4 uvicorn workers
    in the Dockerfile CMD, each loading its own copy of the app; it is a
    baseline cost, not a per-request one, so it did not grow with load and
    would not have been relieved by traffic dropping.

    512 CPU supports 1024-4096 MiB, so this needs no CPU change.
  DESC
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "Number of ECS tasks to run"
  type        = number
  default     = 1
}

variable "health_check_path" {
  description = "HTTP path for ALB health checks"
  type        = string
  default     = "/health"
}

# --- RDS ---

# --- DNS / HTTPS ---

variable "domain_name" {
  description = "Root domain name (e.g. wizscheduler.com). Leave empty to skip DNS/HTTPS setup."
  type        = string
  default     = ""
}

variable "marketing_live" {
  description = "Cutover step B. When true the marketing distribution takes apex + www, the app distribution moves to app.<domain_name>, Route53 and backend URLs follow. Flip only after the marketing site is verified on its CloudFront hostname (see terraform/marketing.tf)."
  type        = bool
  # Flipped true 2026-09-28: marketing site verified live at
  # d3maf6a9zykypv.cloudfront.net, Google OAuth client's authorized
  # JavaScript origins updated to include https://app.wizscheduler.com.
  default = true
}

variable "from_email" {
  # Supplied in CI via TF_VAR_from_email (see .github/workflows/deploy.yml).
  # The local terraform.tfvars is gitignored and NOT read by CI, so set it
  # there — not in tfvars. MUST be on a Resend-verified domain or every send
  # fails. Leave empty to derive noreply@<domain_name>.
  description = "Sender address for transactional email (Resend)."
  type        = string
  default     = ""
}

# --- RDS ---

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "db_name" {
  description = "Name of the PostgreSQL database"
  type        = string
  default     = "wizscheduler"
}

variable "db_username" {
  description = "Master username for the RDS instance. No default: the repo is public, so CI supplies it from the TF_VAR_DB_USERNAME secret. RDS cannot rename the master user, so a different value plans a database replacement."
  type        = string
  sensitive   = true
}

variable "db_password" {
  description = "Master password for the RDS instance"
  type        = string
  sensitive   = true
}

variable "db_allocated_storage" {
  description = "Allocated storage in GB for the RDS instance"
  type        = number
  default     = 20
}

variable "db_engine_version" {
  description = <<-DESC
    PostgreSQL engine version used when the instance is FIRST created. After
    that it is ignored — see the lifecycle block on aws_db_instance.main, which
    ignores engine_version so AWS-applied minor upgrades do not show up as a
    downgrade on the next apply. To do a MAJOR upgrade, drop engine_version
    from that ignore_changes list first.
  DESC
  type        = string
  default     = "15.17"
}

# --- Stripe ---

variable "stripe_price_id" {
  description = "Stripe Price ID for the base subscription (e.g. price_...). Leave empty to defer until set."
  type        = string
  default     = "price_1TVuM9B7IPUEjgxsElcEzZhH"
}

# --- Google OAuth (Sign-In with Google via GIS) ---

variable "google_client_id" {
  description = "Google OAuth 2.0 client ID (e.g. NNNNNNNN-XXXX.apps.googleusercontent.com). Public — exposed in the frontend bundle and used backend-side as the audience claim during ID-token verification. Leave empty to disable Google Sign-In."
  type        = string
  default     = ""
}

# --- Ops alerting ---

variable "ops_alert_email" {
  description = "Email address subscribed to the ops SNS topic for CloudWatch alarms (#49). AWS sends a confirmation link to this address on first apply — the recipient must click it before notifications begin. Leave empty to skip the subscription (alarms still publish to SNS, just nothing listens)."
  type        = string
  default     = ""
}

variable "rls_enforce" {
  description = "Run authenticated requests as app_role under row-level security (#183)"
  type        = bool
  default     = true
}

# --- AI host (#259) ---

variable "agent_reconcile_enabled" {
  description = "Run the daily AI host billing reconcile (agent_reconcile_schedule.tf). Flip with RESERVATIONS_ENABLED."
  type        = bool
  default     = false
}

variable "ftc_ingest_enabled" {
  description = "Run the weekly FTC Do Not Call reported-numbers ingest for call screening (ftc_ingest_schedule.tf)."
  type        = bool
  default     = false
}

variable "ftc_ingest_schedule" {
  description = "EventBridge schedule for the FTC ingest. Default Monday 18:00 UTC, after the FTC's noon Eastern publish."
  type        = string
  default     = "cron(0 18 ? * MON *)"
}

# --- Voice channel (#259 step 6) — everything billable is behind voice_enabled ---

variable "voice_enabled" {
  description = "Create the voice channel: Amazon Connect instance, Lex V2 bot, bridge Lambda, secret and budget (voice.tf). Off by default; nothing billable exists while false. See terraform/README.md 'Voice channel'."
  type        = bool
  default     = false
}

variable "voice_provisioning_enabled" {
  description = "Sets VOICE_PROVISIONING_ENABLED in the ECS task (only when voice_enabled). True lets the app claim Connect numbers, which are billed daily."
  type        = bool
  default     = false
}

variable "voice_bridge_zip" {
  description = "Path to the bridge Lambda zip (CI builds build/voice_bridge.zip from infra/voice/bridge). Read only when voice_enabled."
  type        = string
  default     = ""
}

variable "voice_lex_bot_alias_arn" {
  description = "Lex V2 bot alias ARN with the bridge Lambda as its code hook, created by terraform/voice/publish_lex_bot.sh (the aws provider has no Lex V2 alias resource). Empty: the contact flow is not created yet."
  type        = string
  default     = ""
}

variable "voice_polly_voice" {
  description = "Amazon Polly voice for the Lex locale and the contact flow."
  type        = string
  default     = "Joanna"
}

variable "voice_budget_usd" {
  description = "Monthly AWS Budget for Connect + Lex + Polly + Lambda; email at 80% to ops_alert_email."
  type        = number
  default     = 50
}
