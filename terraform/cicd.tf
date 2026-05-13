# -----------------------------------------------------------------------------
# CI/CD IAM User — least-privilege for GitHub Actions
# Allows: ECR login/push, ECS task-def read + service deploy, S3 frontend
# sync, CloudFront invalidation, and terraform state read/write (S3 + DDB lock).
# -----------------------------------------------------------------------------

resource "aws_iam_user" "cicd" {
  name = "${var.app_name}-cicd"

  tags = { Name = "${var.app_name}-cicd" }
}

resource "aws_iam_access_key" "cicd" {
  user = aws_iam_user.cicd.name
}

data "aws_iam_policy_document" "cicd" {
  # ECR — authenticate and push images
  statement {
    sid = "ECRAuth"
    actions = [
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ECRPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  # ECS — read task definition and deploy new revision
  statement {
    sid = "ECSDescribe"
    actions = [
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ECSDeploy"
    actions = [
      "ecs:RegisterTaskDefinition",
      "ecs:UpdateService",
    ]
    resources = ["*"]
  }

  # IAM — pass execution and task roles to ECS
  statement {
    sid     = "PassRole"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.ecs_execution.arn,
      aws_iam_role.ecs_task.arn,
    ]
  }

  # S3 — sync built frontend assets
  statement {
    sid = "S3FrontendSync"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.frontend.arn]
  }

  statement {
    sid = "S3FrontendObjects"
    actions = [
      "s3:PutObject",
      "s3:PutObjectAcl",
      "s3:GetObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]
  }

  # CloudFront — invalidate after deploy
  statement {
    sid = "CloudFrontInvalidate"
    actions = [
      "cloudfront:CreateInvalidation",
      "cloudfront:GetInvalidation",
      "cloudfront:ListInvalidations",
      "cloudfront:GetDistribution",
    ]
    resources = [aws_cloudfront_distribution.frontend.arn]
  }

  # Terraform remote state — list bucket + read/write the state object
  statement {
    sid = "TfStateBucket"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
    ]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  statement {
    sid = "TfStateObject"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.tfstate.arn}/terraform.tfstate"]
  }

  # Terraform state locking — full access to the lock table only.
  # `dynamodb:*` is acceptable here because the table is single-purpose
  # (terraform locks) and cannot reach any other DynamoDB resources.
  statement {
    sid       = "TfStateLock"
    actions   = ["dynamodb:*"]
    resources = [aws_dynamodb_table.tflock.arn]
  }
}

resource "aws_iam_user_policy" "cicd" {
  name   = "${var.app_name}-cicd-policy"
  user   = aws_iam_user.cicd.name
  policy = data.aws_iam_policy_document.cicd.json
}

# -----------------------------------------------------------------------------
# Broad terraform-management permissions for the cicd user.
#
# Why: `terraform plan`/`apply` refreshes every resource in state, which means
# the runner needs Read access on every AWS service we manage (and Write to
# actually apply changes). The narrow per-service statements above are
# insufficient for full terraform operations.
#
# How: attach AWS's managed PowerUserAccess (everything except IAM,
# Organizations, and Identity Center) + a scoped inline policy for the IAM
# resources terraform manages here (the wizscheduler-* roles + cicd user
# itself). Safer than AdministratorAccess — IAM operations are confined to
# wizscheduler-* ARNs.
# -----------------------------------------------------------------------------

resource "aws_iam_user_policy_attachment" "cicd_power_user" {
  user       = aws_iam_user.cicd.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "cicd_iam" {
  statement {
    sid = "IAMRoleManagement"
    actions = [
      "iam:GetRole",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:GetRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = [
      "arn:aws:iam::*:role/${var.app_name}-*",
    ]
  }

  statement {
    sid = "IAMUserManagement"
    actions = [
      "iam:GetUser",
      "iam:CreateUser",
      "iam:DeleteUser",
      "iam:UpdateUser",
      "iam:TagUser",
      "iam:UntagUser",
      "iam:ListUserTags",
      "iam:GetUserPolicy",
      "iam:PutUserPolicy",
      "iam:DeleteUserPolicy",
      "iam:ListUserPolicies",
      "iam:AttachUserPolicy",
      "iam:DetachUserPolicy",
      "iam:ListAttachedUserPolicies",
      "iam:CreateAccessKey",
      "iam:DeleteAccessKey",
      "iam:UpdateAccessKey",
      "iam:ListAccessKeys",
      "iam:GetAccessKeyLastUsed",
    ]
    resources = [
      "arn:aws:iam::*:user/${var.app_name}-*",
    ]
  }

  # Read-only on the full IAM policy catalog so terraform can resolve managed
  # policy ARNs (PowerUserAccess, AmazonECSTaskExecutionRolePolicy, etc).
  statement {
    sid = "IAMReadPolicies"
    actions = [
      "iam:ListPolicies",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
    ]
    resources = ["*"]
  }
}

# Customer-managed (not inline) because the IAM-management statements push us
# over the 2048-byte inline-policy-per-user limit. Customer-managed policies
# have a 6144-byte cap per version + can be attached without consuming the
# user's inline-policy budget.
resource "aws_iam_policy" "cicd_iam" {
  name        = "${var.app_name}-cicd-iam-policy"
  description = "IAM management permissions for the wizscheduler-cicd user (scoped to wizscheduler-* resources)"
  policy      = data.aws_iam_policy_document.cicd_iam.json
}

resource "aws_iam_user_policy_attachment" "cicd_iam" {
  user       = aws_iam_user.cicd.name
  policy_arn = aws_iam_policy.cicd_iam.arn
}

# -----------------------------------------------------------------------------
# Outputs — use these to set GitHub Actions secrets
# -----------------------------------------------------------------------------

output "cicd_access_key_id" {
  description = "Access key ID for the CI/CD IAM user (set as GitHub secret AWS_ACCESS_KEY_ID)"
  value       = aws_iam_access_key.cicd.id
  sensitive   = true
}

output "cicd_secret_access_key" {
  description = "Secret access key for the CI/CD IAM user (set as GitHub secret AWS_SECRET_ACCESS_KEY)"
  value       = aws_iam_access_key.cicd.secret
  sensitive   = true
}
