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

Secrets are stored in AWS Secrets Manager and injected into the ECS task definition at runtime:

| Secret | Path | Notes |
|--------|------|-------|
| DATABASE_URL | `wizscheduler/prod/DATABASE_URL` | Auto-constructed from RDS endpoint |
| SECRET_KEY | `wizscheduler/prod/SECRET_KEY` | JWT signing key -- replace placeholder after deploy |
| ANTHROPIC_API_KEY | `wizscheduler/prod/ANTHROPIC_API_KEY` | Replace placeholder after deploy |
| RESEND_API_KEY | `wizscheduler/prod/RESEND_API_KEY` | Replace placeholder after deploy |
| DEMO_SEED_PASSWORD | `wizscheduler/prod/DEMO_SEED_PASSWORD` | Demo account login -- replace placeholder after deploy |
| STRIPE_SECRET_KEY | `wizscheduler/prod/STRIPE_SECRET_KEY` | Replace placeholder after deploy |
| STRIPE_WEBHOOK_SECRET | `wizscheduler/prod/STRIPE_WEBHOOK_SECRET` | Replace placeholder after deploy |
| CHECKIN_QR_SECRET | `wizscheduler/prod/CHECKIN_QR_SECRET` | **Must be replaced -- see below** |

The ECS execution role has a scoped IAM policy granting `secretsmanager:GetSecretValue` on these
secrets only.

### `CHECKIN_QR_SECRET` must be replaced before check-in is announced

This is the HMAC key behind the employee check-in rotating QR code. Terraform seeds it with the
same `CHANGE_ME_AFTER_DEPLOY` placeholder as the other secrets, because an ECS task referencing a
secret with no value **fails to start** -- which would take the whole service down rather than
merely disabling check-in.

Unlike the other placeholders, this one is not merely useless: the string is committed to this
repository, so a deployment still running on it can have its check-in codes forged by anyone who
can read the source. The application therefore treats that exact value as unset and refuses to
issue codes (`backend/services/check_in_token.py`). Check-in stays off until you run:

```bash
aws secretsmanager put-secret-value \
  --secret-id wizscheduler/prod/CHECKIN_QR_SECRET \
  --secret-string "$(openssl rand -base64 32)"
```

### `FRONTEND_URL` is not a secret, but it is load-bearing

`ecs.tf` sets it from `var.domain_name` (`https://<domain_name>`). It is the origin encoded into
every check-in QR code, and the trusted base for every emailed link that carries a single-use token
(password reset, email verification, manager and employee invites). Those links deliberately do NOT
follow the request's `Origin`/`Referer`/`Host` — a forged header would otherwise make us mail a
victim a genuine link pointing at an attacker's host. `CORS_ORIGINS` doubles as the allowlist of
additional origins that ARE honoured.

It gets its own note because its failure mode used to be **silent**: the application default is
`http://localhost:5173`, so a missing value produced QR codes that rendered correctly, scanned
correctly on an employee's phone, and opened a browser to nobody's server. Nothing refused,
nothing 500'd, and the only symptom was employees quietly unable to check in.
`check_in_deep_link` now rejects any non-absolute value rather than emitting a dead link, so the
failure is loud -- but if `var.domain_name` is empty this variable resolves to `""` and check-in
will refuse to issue codes. Set `domain_name` for any environment where check-in is enabled.

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

## Voice channel (#259 step 6) — off by default

`voice.tf` holds the AI host's phone line: an Amazon Connect instance, a
Lex V2 bot (`en_US`, one `HostTurn` intent), the bridge Lambda
(`infra/voice/bridge`), its Secrets Manager secret, an ECS task-role policy
for number provisioning, and a monthly AWS Budget. Every block is gated on
`voice_enabled` (default `false`; `tests/infra/test_terraform_voice_gated.py`
checks it), and the ECS task definition only gains `VOICE_BRIDGE_SECRET` and
`CONNECT_*` / `VOICE_PROVISIONING_ENABLED` when it is on, so with voice off a
plan shows no voice resources and no task-definition change. CI zips the
Lambda into `build/voice_bridge.zip` and passes it as `voice_bridge_zip` on
every plan/apply; nothing reads it while voice is off.

`agent_reconcile_schedule.tf` (the daily `run_agent_overage_reconcile` task)
is gated separately on `agent_reconcile_enabled` (default `false`); flip it
when reservations launch.

### Turning voice on

The aws provider 5.x has no Lex V2 bot alias resource, its
`aws_connect_bot_association` takes only Lex V1 bots, it cannot manage the
built-in `FallbackIntent`, and a bot version cannot be published until the
locale is built. Those steps live in `voice/publish_lex_bot.sh`, between two
applies:

1. Add `TF_VAR_voice_enabled: "true"` to both terraform jobs in
   `.github/workflows/deploy.yml` and merge. This creates the instance, bot,
   Lambda, secret (seeded with a random value, so nothing to set by hand)
   and budget, and redeploys the service with `VOICE_BRIDGE_SECRET`.
2. From `terraform/`, with admin credentials: `./voice/publish_lex_bot.sh`.
   It enables the FallbackIntent code hooks, builds `en_US`, publishes a
   version, points alias `live` at it with the bridge Lambda as code hook,
   associates the alias with Connect, and prints the alias ARN. Re-run it
   after any change to the bot, intent or slots (Connect keeps using the
   old version until then).
3. Add `TF_VAR_voice_lex_bot_alias_arn: "<that ARN>"` to both jobs and merge.
   This creates the contact flow (`voice/contact_flow.json.tftpl`) and sets
   `CONNECT_CONTACT_FLOW_ID` in the task.
4. Open the flow once in the Connect console (Routing -> Flows) and check it
   loads without warnings; it was written by hand in flow language.
5. Only then set `TF_VAR_voice_provisioning_enabled: "true"`: the app starts
   claiming numbers, each billed daily. With it off (the default) the app
   claims and releases nothing.

The contact flow sets Lex session attributes `contact_id`, `dialed` and
`caller`, then, on every exit from the bot, transfers to
`$.Lex.SessionAttributes.transfer_to` when the backend handed off and
otherwise disconnects. It does **not** set `fallback_transfer_to`: the flow
cannot look up a location's staff number, so when the bridge itself fails
the Lambda tells the caller to call back. Backend handoffs still transfer.

The budget (`voice_budget_usd`, default 50) covers Amazon Connect, Lex,
Polly and Lambda and emails `ops_alert_email` at 80% of actual spend. It
alerts; it does not stop anything. Staff transfers are outbound calls billed
per minute (the instance has `outbound_calls_enabled` for them); the app
bounds them (US and Canadian numbers only, no premium-rate or service area
codes, never one of our AI host numbers, at most `VOICE_TRANSFERS_PER_DAY`
per location per day: `backend/reservations/voice_transfers.py`), and the
budget is the backstop.

Contact flow logs carry caller numbers. Terraform creates their log group,
`/aws/connect/<instance alias>`, with a 30-day retention before the
instance, so Connect writes into it rather than creating one that never
expires.

Each run of `publish_lex_bot.sh` publishes a new bot version and leaves the
old ones. Lex keeps at most 100 versions per bot; prune unused ones now and
then with `aws lexv2-models delete-bot-version` (never the one alias `live`
points at).

To turn voice off, first release the claimed numbers while
`voice_provisioning_enabled` is still true (the app releases a number only
with provisioning on: end the subscriptions and let the daily run release
them after their hold), or release them in the Connect console (Channels ->
Phone numbers). Only then set `voice_provisioning_enabled` false: numbers
still claimed at that point stay claimed, and billed daily. Then delete the bot alias (`aws lexv2-models
delete-bot-alias`) and published versions, then apply with `voice_enabled`
false. The bridge secret goes into Secrets Manager's 7-day recovery window,
so turning voice back on within a week fails on the name; either wait, or
delete it outright first:
`aws secretsmanager delete-secret --secret-id wizscheduler/prod/VOICE_BRIDGE_SECRET --force-delete-without-recovery`.

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
| `voice_enabled` | `false` | Create the voice channel (`voice.tf`) |
| `voice_provisioning_enabled` | `false` | Let the app claim Connect numbers (only with voice on) |
| `voice_bridge_zip` | `""` | Bridge Lambda zip; CI builds it |
| `voice_lex_bot_alias_arn` | `""` | Lex alias from `voice/publish_lex_bot.sh`; empty skips the contact flow |
| `voice_polly_voice` | `Joanna` | Polly voice (neural) |
| `voice_budget_usd` | `50` | Monthly voice budget; 80% email alert |
| `agent_reconcile_enabled` | `false` | Daily AI host billing reconcile schedule |

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

## Database TLS (#203)

The app verifies the RDS server certificate: `DB_SSL_ROOT_CERT` (set in `ecs.tf`) points at `backend/certs/rds-global-bundle.pem`, AWS's global RDS CA bundle, which ships in the image. The instance uses CA `rds-ca-rsa2048-g1`; RDS rotates the server certificate itself. If AWS issues a new CA or the instance moves to one, refresh the bundle from `https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem` **before** switching the instance's CA, or every connection will fail verification. Rollback: set `DB_SSL_ROOT_CERT` to an empty string.

The server also refuses plaintext: the instance uses the `wizscheduler-postgres15` parameter group (`rds.tf`) with `rds.force_ssl = 1`. A parameter group change only takes effect after a reboot (a few minutes of downtime on this single-AZ instance), so after Terraform applies it, run `aws rds modify-db-instance --db-instance-identifier wizscheduler-db --db-parameter-group-name wizscheduler-postgres15 --apply-immediately` and then `aws rds reboot-db-instance --db-instance-identifier wizscheduler-db`. Any client, including a one-off `psql`, must then connect with `sslmode=require` or stricter. After a major engine upgrade the parameter group family has to change with it.
