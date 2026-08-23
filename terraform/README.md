# WizScheduler AWS Infrastructure

Terraform configuration for deploying WizScheduler to AWS.

## Architecture

```
                              Internet
                                 |
                         +-------+-------+
                         |   CloudFront   |   (global edge, HTTPS)
                         |   OAC -> S3    |   default -> S3 (SPA)
                         |   /api/* -> ALB|   /api/* -> ALB (no cache)
                         +---+-------+---+
                             |       |
                S3 (private) |       | ALB (public subnets, :443)
                frontend     |       | Health: /health
                bucket       |       +-------+
                                             |
                                +------------+------------+
                                |                         |
                       +--------+--------+      +--------+--------+
                       | ECS Fargate     |      | ECS Fargate     |    (Private subnets)
                       | Task (AZ-1)     |      | Task (AZ-2)     |    512 CPU / 1024 MiB
                       | wizscheduler:8000|     | wizscheduler:8000|
                       +--------+---------+     +--------+---------+
                                |                         |
                                +------------+------------+
                                             |
                                      +------+------+
                                      | RDS Postgres |    (Private subnets)
                                      | db.t3.micro  |    Encrypted, 7-day backups
                                      +---------------+
```

Frontend static assets live on S3 (private, OAC-only read) and are served globally by CloudFront. The same CloudFront distribution forwards `/api/*` to the ALB with caching disabled, so the browser always uses a single origin — no CORS configuration required, and NDJSON streaming from `/api/v1/schedules/generate` passes through unbuffered.

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

**`CHECKIN_QR_SECRET` (employee check-in feature) is not yet provisioned here.** It follows the
same pattern as `DEMO_SEED_PASSWORD` -- a value with no safe default that the app refuses to run
without -- but as of this branch there is no `aws_secretsmanager_secret` resource for it in
`secrets.tf` and no corresponding entry in the ECS task definition's `secrets` block in `ecs.tf`.
It is the HMAC key behind the employee check-in rotating QR code (see `CLAUDE.md` and the
README's "Environment variables" table); the service raises rather than issuing or verifying a
check-in code while it is unset -- there is deliberately no fallback default, because a
predictable key is the same as no key. Before the check-in feature is enabled in production, add
a Secrets Manager entry for `wizscheduler/prod/CHECKIN_QR_SECRET`, wire it into the ECS task
definition alongside the secrets above, and set a real value (not the placeholder pattern used
for the other secrets, since there is no safe placeholder for this one -- an unset or guessable
value is a live vulnerability, not just an inconvenience).

**`FRONTEND_URL` also has no provisioning here**, and unlike `CHECKIN_QR_SECRET` it is not a
secret and fails **silently**, not loudly. It is a plain environment variable (see the README's
"Environment variables" table) that must be set to the deployed frontend's origin -- it is the
base of the URL encoded into every check-in QR code. There is no `FRONTEND_URL` entry in
`ecs.tf`'s environment block, so the app falls back to its `http://localhost:5173` default. A QR
code built from that default still renders, still scans cleanly on an employee's phone, and still
opens a browser -- it just opens to nobody's server. Nothing refuses, nothing 500s, and nothing in
the check-in flow itself would surface the mistake; it would only show up as employees quietly
unable to check in. Before the check-in feature is enabled in production, set `FRONTEND_URL` in
the ECS task definition's environment block to the real deployed origin (e.g.
`https://wizscheduler.com`).

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
| S3 Bucket (frontend) | `wizscheduler-frontend-prod` | `frontend.tf` |
| CloudFront Distribution | `wizscheduler-frontend-cdn` | `frontend.tf` |

All resources are tagged with `project = wizscheduler`, `managed_by = terraform`, and `environment = prod`.

## Prerequisites

- [Terraform ~> 1.14.0](https://developer.hashicorp.com/terraform/downloads) — must match `terraform_version` in `.github/workflows/deploy.yml`; see [Terraform version policy](#terraform-version-policy)
- AWS CLI configured with credentials that have permission to create the resources above
- A database password ready (no default is provided)

## Terraform version policy

`required_version` in `main.tf` carries an **upper** bound, and CI pins the same
version in `.github/workflows/deploy.yml`. Both must move together.

The reason is that Terraform stamps its own version into the remote state and
then refuses to read state written by anything newer. With an open-ended
constraint, one `apply` from a newer local CLI silently locks the CI runner out
of its own state, and the next deploy fails with a version error rather than
anything descriptive.

### Rolling back a version bump

State format version has been `4` across all of Terraform 1.x, so a state file
written by a newer 1.x is structurally readable by an older one — the block is
the `terraform_version` stamp, not the contents. Two ways back:

1. **Restore the previous state object.** The state bucket has versioning
   enabled, so every write is retained. List versions and copy the pre-upgrade
   one back over the current object:

   ```bash
   aws s3api list-object-versions --bucket wizscheduler-tfstate \
     --prefix terraform.tfstate \
     --query 'Versions[].{Id:VersionId,Modified:LastModified}' --output table

   aws s3api copy-object --bucket wizscheduler-tfstate --key terraform.tfstate \
     --copy-source 'wizscheduler-tfstate/terraform.tfstate?versionId=<VERSION_ID>'
   ```

   Exact, but the restored state does not know about anything applied after the
   upgrade — roll back promptly or reconcile the drift by hand.

2. **Rewrite the stamp.** Pull the state, set `terraform_version` back, and
   `terraform state push -force` it.

Both assume the config has not started using post-upgrade language features
(ephemeral resources, newer `removed` block forms, provider-defined functions).

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
alb_dns_name               = "wizscheduler-alb-123456.us-east-1.elb.amazonaws.com"
ecr_repository_url         = "123456789.dkr.ecr.us-east-1.amazonaws.com/wizscheduler"
rds_endpoint               = "wizscheduler-db.abc123.us-east-1.rds.amazonaws.com:5432"
ecs_cluster_name           = "wizscheduler-cluster"
ecs_service_name           = "wizscheduler-service"
frontend_bucket            = "wizscheduler-frontend-prod"
cloudfront_distribution_id = "E1ABCDEFGH2345"
cloudfront_domain_name     = "d1a2b3c4d5e6f7.cloudfront.net"
app_url                    = "https://yourdomain.com"   # or the CloudFront domain if no custom domain
```

## Post-Deploy Steps

1. **Update secrets** — replace the three placeholder secrets (SECRET_KEY, ANTHROPIC_API_KEY, RESEND_API_KEY):

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

2. **Push the backend Docker image** to ECR:

   ```bash
   aws ecr get-login-password --region us-east-1 | \
     docker login --username AWS --password-stdin <ECR_REPO_URL>

   docker build -t wizscheduler .
   docker tag wizscheduler:latest <ECR_REPO_URL>:latest
   docker push <ECR_REPO_URL>:latest
   ```

   The container runs `alembic upgrade head` on startup, so migrations apply automatically on the first task.

3. **Build and upload the frontend** to S3, then invalidate CloudFront:

   ```bash
   cd frontend && npm ci && npm run build && cd ..

   BUCKET=$(terraform -chdir=terraform output -raw frontend_bucket)
   DIST=$(terraform -chdir=terraform output -raw cloudfront_distribution_id)

   # Hashed assets — safe to cache forever
   aws s3 sync frontend/dist/ "s3://$BUCKET/" \
     --delete --exclude "index.html" \
     --cache-control "public, max-age=31536000, immutable"

   # index.html — must not be cached so new deploys are picked up immediately
   aws s3 cp frontend/dist/index.html "s3://$BUCKET/index.html" \
     --cache-control "no-cache, no-store, must-revalidate" \
     --content-type "text/html"

   aws cloudfront create-invalidation \
     --distribution-id "$DIST" \
     --paths "/index.html" "/"
   ```

4. **Configure GitHub Actions secrets** so subsequent pushes to `main` deploy automatically. In the repo settings add:

   | Secret | Value |
   |--------|-------|
   | `AWS_ACCESS_KEY_ID` | `terraform output -raw cicd_access_key_id` |
   | `AWS_SECRET_ACCESS_KEY` | `terraform output -raw cicd_secret_access_key` |
   | `AWS_REGION` | `us-east-1` (or your region) |
   | `FRONTEND_BUCKET` | `terraform output -raw frontend_bucket` |
   | `CLOUDFRONT_DISTRIBUTION_ID` | `terraform output -raw cloudfront_distribution_id` |

5. **Seed data (one-time)** — if you need demo data:

   ```bash
   aws ecs execute-command \
     --cluster wizscheduler-cluster \
     --task <TASK_ID> \
     --container wizscheduler \
     --interactive \
     --command "python -m backend.seed"
   ```

6. **Verify** — hit the app:

   ```bash
   # Health check via CloudFront (/api/* is proxied to ECS)
   curl https://<CLOUDFRONT_DOMAIN_OR_CUSTOM_DOMAIN>/api/v1/health
   # or hit the ALB directly to bypass CloudFront:
   curl https://<ALB_DNS_NAME>/health
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
| `db_engine_version` | `15` | PostgreSQL MAJOR version (minor drift is absorbed; see variables.tf) |

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
| S3 (frontend bucket) | ~$0.10 |
| CloudFront (PriceClass_100) | ~$1–5 at low traffic (first 1 TB/mo free) |
| **Total (idle/low traffic)** | **~$85–95/mo** |
