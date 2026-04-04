# WizScheduler AWS Infrastructure

Terraform configuration for deploying WizScheduler to AWS.

## Architecture

```
                        Internet
                           |
                    +------+------+
                    |     ALB     |    (Public subnets, HTTP :80)
                    | Health: /health
                    +------+------+
                           |
              +------------+------------+
              |                         |
     +--------+--------+      +--------+--------+
     |  ECS Fargate     |      |  ECS Fargate     |    (Private subnets)
     |  Task (AZ-1)     |      |  Task (AZ-2)     |    512 CPU / 1024 MiB
     |  wizscheduler:8000|     |  wizscheduler:8000|
     +--------+---------+      +--------+---------+
              |                         |
              +------------+------------+
                           |
                    +------+------+
                    |  RDS Postgres |    (Private subnets)
                    |  db.t3.micro  |    Encrypted, 7-day backups
                    +---------------+
```

### Network Layout

| Subnet | CIDR | Purpose |
|--------|------|---------|
| Public (AZ-1) | 10.0.0.0/24 | ALB, NAT Gateway |
| Public (AZ-2) | 10.0.1.0/24 | ALB |
| Private (AZ-1) | 10.0.100.0/24 | ECS tasks, RDS |
| Private (AZ-2) | 10.0.101.0/24 | ECS tasks, RDS |

A single NAT Gateway in the first public subnet provides outbound internet access for private subnets (Anthropic API calls, email sending). This keeps costs low; for production HA, add a NAT per AZ.

### Security Groups

| Group | Inbound | Source |
|-------|---------|--------|
| ALB | TCP 80, 443 | 0.0.0.0/0 |
| ECS | TCP 8000 | ALB SG only |
| RDS | TCP 5432 | ECS SG only |

No direct internet access to ECS tasks or the database.

### Secrets Management

Four secrets are stored in AWS Secrets Manager and injected into the ECS task definition at runtime:

| Secret | Path | Notes |
|--------|------|-------|
| DATABASE_URL | `wizscheduler/prod/DATABASE_URL` | Auto-constructed from RDS endpoint |
| SECRET_KEY | `wizscheduler/prod/SECRET_KEY` | JWT signing key -- replace placeholder after deploy |
| ANTHROPIC_API_KEY | `wizscheduler/prod/ANTHROPIC_API_KEY` | Replace placeholder after deploy |
| RESEND_API_KEY | `wizscheduler/prod/RESEND_API_KEY` | Replace placeholder after deploy |

The ECS execution role has a scoped IAM policy granting `secretsmanager:GetSecretValue` on only these four secrets.

### Logging

Container stdout/stderr is shipped to CloudWatch Logs via the `awslogs` driver.

- Log group: `/ecs/wizscheduler`
- Retention: 30 days
- Stream prefix: `ecs/`
- Container Insights enabled on the ECS cluster

The app outputs structured JSON logs in production for easy CloudWatch Insights queries.

## Resources Created

| Resource | Name | File |
|----------|------|------|
| VPC + Subnets + NAT | `wizscheduler-vpc` | `vpc.tf` |
| Application Load Balancer | `wizscheduler-alb` | `alb.tf` |
| ECS Cluster + Service + Task | `wizscheduler-cluster` | `ecs.tf` |
| ECR Repository | `wizscheduler` | `ecr.tf` |
| RDS PostgreSQL | `wizscheduler-db` | `rds.tf` |
| Secrets Manager (x4) | `wizscheduler/prod/*` | `secrets.tf` |
| CloudWatch Log Group | `/ecs/wizscheduler` | `cloudwatch.tf` |
| IAM Roles (execution + task) | `wizscheduler-ecs-*` | `ecs.tf` |

All resources are tagged with `project = wizscheduler`, `managed_by = terraform`, and `environment = prod`.

## Prerequisites

- [Terraform >= 1.5](https://developer.hashicorp.com/terraform/downloads)
- AWS CLI configured with credentials that have permission to create the resources above
- A database password ready (no default is provided)

## Deploy

```bash
cd terraform

# Initialize providers
terraform init

# Preview changes
terraform plan -var="db_password=YOUR_SECURE_PASSWORD"

# Apply
terraform apply -var="db_password=YOUR_SECURE_PASSWORD"
```

After `apply` completes, Terraform outputs the values you need:

```
alb_dns_name       = "wizscheduler-alb-123456.us-east-1.elb.amazonaws.com"
ecr_repository_url = "123456789.dkr.ecr.us-east-1.amazonaws.com/wizscheduler"
rds_endpoint       = "wizscheduler-db.abc123.us-east-1.rds.amazonaws.com:5432"
ecs_cluster_name   = "wizscheduler-cluster"
ecs_service_name   = "wizscheduler-service"
```

## Post-Deploy Steps

1. **Update secrets** -- Replace the three placeholder secrets (SECRET_KEY, ANTHROPIC_API_KEY, RESEND_API_KEY) via the AWS console or CLI:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id wizscheduler/prod/SECRET_KEY \
     --secret-string "$(openssl rand -hex 32)"

   aws secretsmanager put-secret-value \
     --secret-id wizscheduler/prod/ANTHROPIC_API_KEY \
     --secret-string "sk-ant-..."

   aws secretsmanager put-secret-value \
     --secret-id wizscheduler/prod/RESEND_API_KEY \
     --secret-string "re_..."
   ```

2. **Push the Docker image** to ECR:

   ```bash
   # Authenticate Docker to ECR
   aws ecr get-login-password --region us-east-1 | \
     docker login --username AWS --password-stdin <ECR_REPO_URL>

   # Build and push
   docker build -t wizscheduler .
   docker tag wizscheduler:latest <ECR_REPO_URL>:latest
   docker push <ECR_REPO_URL>:latest
   ```

3. **Run database migrations** -- The container runs `alembic upgrade head` on startup automatically.

4. **Seed data (one-time)** -- If you need demo data:

   ```bash
   aws ecs execute-command \
     --cluster wizscheduler-cluster \
     --task <TASK_ID> \
     --container wizscheduler \
     --interactive \
     --command "python -m backend.seed"
   ```

5. **Verify** -- Hit the ALB DNS name:

   ```bash
   curl http://<ALB_DNS_NAME>/health
   # {"status":"healthy"}
   ```

## Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `aws_region` | `us-east-1` | AWS region |
| `app_name` | `wizscheduler` | Resource naming prefix |
| `environment` | `prod` | Environment tag |
| `vpc_cidr` | `10.0.0.0/16` | VPC CIDR block |
| `az_count` | `2` | Number of availability zones |
| `container_port` | `8000` | App listening port |
| `container_cpu` | `512` | Fargate CPU units (512 = 0.5 vCPU) |
| `container_memory` | `1024` | Fargate memory in MiB |
| `desired_count` | `1` | Number of ECS tasks |
| `health_check_path` | `/health` | ALB health check path |
| `db_instance_class` | `db.t3.micro` | RDS instance size |
| `db_name` | `wizscheduler` | PostgreSQL database name |
| `db_username` | `wizadmin` | RDS master username |
| `db_password` | *(required)* | RDS master password |
| `db_allocated_storage` | `20` | RDS storage in GB |
| `db_engine_version` | `15.4` | PostgreSQL version |

## Scaling Up

To scale beyond the defaults:

- **Horizontal**: Increase `desired_count` to run more ECS tasks behind the ALB.
- **Vertical**: Bump `container_cpu` / `container_memory` and `db_instance_class`.
- **HA NAT**: Add a NAT Gateway per AZ by duplicating the NAT resource per `az_count`.
- **HTTPS**: Add an ACM certificate and an HTTPS listener on port 443, then redirect HTTP to HTTPS.
- **Remote state**: Uncomment the S3 backend block in `main.tf` and create the S3 bucket + DynamoDB table for state locking.

## Cost Estimate (us-east-1, defaults)

| Resource | Approximate Monthly Cost |
|----------|-------------------------|
| NAT Gateway | ~$32 + data transfer |
| ECS Fargate (1 task, 0.5 vCPU / 1 GB) | ~$18 |
| RDS db.t3.micro | ~$15 |
| ALB | ~$16 + LCU charges |
| Secrets Manager (4 secrets) | ~$2 |
| CloudWatch Logs | ~$0.50/GB ingested |
| ECR | ~$0.10/GB stored |
| **Total (idle/low traffic)** | **~$85/mo** |
