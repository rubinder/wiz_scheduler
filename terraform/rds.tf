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
# -----------------------------------------------------------------------------

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
