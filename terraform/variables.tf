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
  description = "Memory (MiB) for the Fargate task"
  type        = number
  default     = 1024
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
  description = "Master username for the RDS instance"
  type        = string
  default     = "wizzzadmin"
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
  description = "PostgreSQL engine version"
  type        = string
  default     = "15.13"
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
