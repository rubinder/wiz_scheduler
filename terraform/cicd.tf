# -----------------------------------------------------------------------------
# CI/CD IAM User — least-privilege for GitHub Actions
# Only allows: ECR login/push, ECS task-def read + service deploy
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
}

resource "aws_iam_user_policy" "cicd" {
  name   = "${var.app_name}-cicd-policy"
  user   = aws_iam_user.cicd.name
  policy = data.aws_iam_policy_document.cicd.json
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
