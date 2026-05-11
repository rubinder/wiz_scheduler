output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for pushing Docker images"
  value       = aws_ecr_repository.app.repository_url
}

output "rds_endpoint" {
  description = "Endpoint of the RDS PostgreSQL instance"
  value       = aws_db_instance.main.endpoint
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = aws_ecs_service.app.name
}

output "app_url" {
  description = "Application URL (HTTPS if domain configured, otherwise ALB DNS)"
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}"
}

output "route53_nameservers" {
  description = "Nameservers for the Route 53 hosted zone (set these at your domain registrar)"
  value       = var.domain_name != "" ? aws_route53_zone.main[0].name_servers : []
}

output "frontend_bucket" {
  description = "S3 bucket for built frontend assets (target of `aws s3 sync`)"
  value       = aws_s3_bucket.frontend.bucket
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (use for cache invalidation)"
  value       = aws_cloudfront_distribution.frontend.id
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name (use if no custom domain)"
  value       = aws_cloudfront_distribution.frontend.domain_name
}
