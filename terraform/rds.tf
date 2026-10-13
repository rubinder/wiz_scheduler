# -----------------------------------------------------------------------------
# RDS Subnet Group
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${var.app_name}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${var.app_name}-db-subnet-group" }
}

# -----------------------------------------------------------------------------
# RDS Security Group — only accessible from ECS tasks
# -----------------------------------------------------------------------------

resource "aws_security_group" "rds" {
  name        = "${var.app_name}-rds-sg"
  description = "Allow PostgreSQL access from ECS tasks only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from ECS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.app_name}-rds-sg" }
}

# -----------------------------------------------------------------------------
# RDS PostgreSQL Instance
#
# Note: Master user connects as var.db_username (typically "postgres").
# Application roles (app_role, analytics_role, admin_role) are created via
# Alembic migration running as the master user, not via Terraform. This keeps
# role lifecycle under code control and tracks them in version control.
# See backend/alembic/versions/0044_add_rls_policies.py
#
# Roles created by migration:
#  - app_role: SELECT, INSERT, UPDATE, DELETE (RLS enforced)
#  - analytics_role: SELECT only (full access to all data)
#  - admin_role: SELECT, UPDATE (full access to all data)
#
# Credentials:
#  - Master user: stored in AWS Secrets Manager as DATABASE_URL
#  - app_role: stored as APP_DATABASE_URL (created by this Terraform)
#  - analytics_role & admin_role: created manually as needed
#
# ECS tasks use app_role (via APP_DATABASE_URL environment variable).
# Background jobs use app_role or admin_role depending on requirements.
# Alembic migrations use master user (DATABASE_URL).
#
# To generate passwords for roles, use:
#  openssl rand -base64 32
# Then manually UPDATE postgres roles or use psql.
#
# RLS policies are created by Alembic migration that runs as master user,
# ensuring they're version-controlled and can be rolled back.
#
# See terraform/variables.tf for app_db_username and app_db_password inputs.
# These default to placeholders and should be set via TF_VAR_ environment
# variables or terraform.tfvars in production.
#
# After Alembic migration creates roles, the role passwords can be rotated
# by updating terraform variables and re-applying:
#  terraform apply -var=app_db_password="new_password"
# which updates APP_DATABASE_URL in Secrets Manager and triggers ECS
# task redeploys via CI/CD.
#
# Historical note (Part 1): Application-level tenant filtering via
# ContextVar in backend/dependencies.py sets company_ids per request.
# This Terraform change (Part 2) adds Postgres RLS for defense-in-depth.
# Both layers together catch missing WHERE clauses, raw SQL, and bypasses.
#
# RFC: https://github.com/rubinder/wiz_scheduler/issues/125
# PR #164: Part 1 (ORM filtering)
# PR TBD: Part 2 (Postgres RLS, this change + Alembic migration)
#
# MANUAL STEP REQUIRED BEFORE TERRAFORM APPLY:
#  1. In your environment, set TF_VAR_app_db_username and TF_VAR_app_db_password
#  2. Run terraform plan and review
#  3. Apply terraform (creates APP_DATABASE_URL secret)
#  4. Deploy code with Alembic migration (creates roles + RLS policies)
#  5. Update ECS task definition to use APP_DATABASE_URL from Secrets Manager
#     (this file already does that; just run terraform apply)
#  6. Blue-green deploy the new task definition
#
# Rollback (if needed):
#  1. Revert ECS to old task definition (use DATABASE_URL again)
#  2. Drop RLS policies: ALEMBIC DOWN or manual DROP POLICY commands
#  3. Revert to master user connection in config
#
# ---------------------------------------------------------------------------

resource "aws_db_instance" "main" {
  identifier = "${var.app_name}-db"

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 2
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az            = false
  publicly_accessible = false
  skip_final_snapshot = true

  # AWS applies minor version upgrades during the maintenance window (this was
  # already on by default; it is spelled out here because the lifecycle block
  # below depends on it).
  auto_minor_version_upgrade = true

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:30-sun:05:30"

  tags = { Name = "${var.app_name}-db" }

  lifecycle {
    # AWS owns the minor version (auto_minor_version_upgrade above). Without
    # this, every apply after a maintenance-window patch tries to move the
    # instance back to the pinned version and RDS rejects it:
    #   InvalidParameterCombination: Cannot upgrade postgres from 15.17 to 15.13
    # which fails the whole deploy. Seen in production on 2026-08-20.
    # For a deliberate MAJOR upgrade, remove engine_version from this list,
    # set allow_major_version_upgrade, and apply.
    ignore_changes = [engine_version]
  }
}
