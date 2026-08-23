# -----------------------------------------------------------------------------
# ECS Security Group — only accessible from ALB
# -----------------------------------------------------------------------------

resource "aws_security_group" "ecs" {
  name        = "${var.app_name}-ecs-sg"
  description = "Allow inbound traffic from ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "App port from ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.app_name}-ecs-sg" }
}

# -----------------------------------------------------------------------------
# ECS Cluster
# -----------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = "${var.app_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.app_name}-cluster" }
}

# -----------------------------------------------------------------------------
# IAM — Task Execution Role (pulls images, reads secrets, writes logs)
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.app_name}-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = { Name = "${var.app_name}-ecs-execution-role" }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_base" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "secrets_access" {
  statement {
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.secret_key.arn,
      aws_secretsmanager_secret.anthropic_api_key.arn,
      aws_secretsmanager_secret.resend_api_key.arn,
      aws_secretsmanager_secret.stripe_secret_key.arn,
      aws_secretsmanager_secret.stripe_webhook_secret.arn,
      aws_secretsmanager_secret.demo_seed_password.arn,
    ]
  }
}

resource "aws_iam_role_policy" "ecs_secrets" {
  name   = "${var.app_name}-ecs-secrets-policy"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.secrets_access.json
}

# -----------------------------------------------------------------------------
# IAM — Task Role (permissions the running container gets)
# -----------------------------------------------------------------------------

resource "aws_iam_role" "ecs_task" {
  name               = "${var.app_name}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = { Name = "${var.app_name}-ecs-task-role" }
}

# -----------------------------------------------------------------------------
# ECS Task Definition
# -----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "app" {
  family                   = var.app_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = var.app_name
      image     = "${aws_ecr_repository.app.repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          hostPort      = var.container_port
          protocol      = "tcp"
        }
      ]

      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = aws_secretsmanager_secret.database_url.arn
        },
        {
          name      = "SECRET_KEY"
          valueFrom = aws_secretsmanager_secret.secret_key.arn
        },
        {
          name      = "ANTHROPIC_API_KEY"
          valueFrom = aws_secretsmanager_secret.anthropic_api_key.arn
        },
        {
          name      = "RESEND_API_KEY"
          valueFrom = aws_secretsmanager_secret.resend_api_key.arn
        },
        {
          name      = "STRIPE_SECRET_KEY"
          valueFrom = aws_secretsmanager_secret.stripe_secret_key.arn
        },
        {
          name      = "STRIPE_WEBHOOK_SECRET"
          valueFrom = aws_secretsmanager_secret.stripe_webhook_secret.arn
        },
        {
          # Consumed by backend/seed.py so the demo accounts are never created
          # with the local-dev default password.
          name      = "DEMO_SEED_PASSWORD"
          valueFrom = aws_secretsmanager_secret.demo_seed_password.arn
        },
        {
          # HMAC key for the rotating check-in QR. The app refuses to issue
          # codes while this is unset or still the Terraform placeholder.
          name      = "CHECKIN_QR_SECRET"
          valueFrom = aws_secretsmanager_secret.checkin_qr_secret.arn
        },
      ]

      environment = [
        {
          name  = "PORT"
          value = tostring(var.container_port)
        },
        {
          name  = "ENVIRONMENT"
          value = var.environment
        },
        {
          name  = "ENV"
          value = "production"
        },
        {
          name  = "CORS_ORIGINS"
          value = var.domain_name != "" ? "https://${var.domain_name}" : "*"
        },
        {
          name  = "STRIPE_PRICE_ID"
          value = var.stripe_price_id
        },
        {
          # The origin encoded into every check-in QR code. Not a secret, but
          # load-bearing: the app's default is http://localhost:5173, so a
          # missing value here would produce codes that scan cleanly and open
          # nothing. check_in_deep_link refuses a non-absolute value rather
          # than emitting a dead link.
          name  = "FRONTEND_URL"
          value = var.domain_name != "" ? "https://${var.domain_name}" : ""
        },
        {
          name  = "STRIPE_SUCCESS_URL"
          value = var.domain_name != "" ? "https://${var.domain_name}/register?session_id={CHECKOUT_SESSION_ID}" : ""
        },
        {
          name  = "STRIPE_CANCEL_URL"
          value = var.domain_name != "" ? "https://${var.domain_name}/register" : ""
        },
        {
          name  = "GOOGLE_CLIENT_ID"
          value = var.google_client_id
        },
        {
          # Sender for all transactional email. MUST be on a Resend-verified
          # domain — an unverified domain makes every send throw (see the
          # placeholder default in backend/config.py). Explicit var.from_email
          # wins; otherwise derive noreply@<domain_name>.
          name  = "FROM_EMAIL"
          value = var.from_email != "" ? var.from_email : (var.domain_name != "" ? "noreply@${var.domain_name}" : "")
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "${var.app_name}-task" }
}

# -----------------------------------------------------------------------------
# ECS Service
# -----------------------------------------------------------------------------

resource "aws_ecs_service" "app" {
  name            = "${var.app_name}-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.app_name
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.http, aws_lb_listener.https]

  tags = { Name = "${var.app_name}-service" }

  lifecycle {
    # The deploy-backend job in .github/workflows/deploy.yml owns which task
    # definition is deployed: it registers a revision pinned to the immutable
    # image tag (the commit SHA) and points the service at it. The revision
    # terraform knows about pins ":latest" instead, so without this every apply
    # drags the service back off the SHA-pinned revision — the running code is
    # the same bits, but the service no longer says which commit it is running,
    # and infra changes trigger an unrelated rollout.
    #
    # terraform still owns the task definition itself (image, cpu, memory, env,
    # secrets); a change there registers a new revision that the next
    # deploy-backend run picks up as its base.
    ignore_changes = [task_definition]
  }
}
