# -----------------------------------------------------------------------------
# Marketing site hosting — private S3 bucket + its own CloudFront distribution.
#
# The marketing site (repo wiz_scheduler_marketing) is a static Astro build
# that takes over the apex domain at cutover; the app moves to app.<domain>.
# Two-step cutover, both driven from here:
#
#   step A (var.marketing_live = false, default): bucket, distribution with
#          no aliases, CloudFront Function, deploy IAM user. The marketing
#          repo deploys to the *.cloudfront.net hostname for verification.
#   step B (var.marketing_live = true): this distribution takes apex + www,
#          the app distribution moves to app.<domain> (frontend.tf), Route53
#          follows (dns.tf), and backend URLs follow (ecs.tf via local.app_host).
#
# Deploys are done by the marketing repo's GitHub Actions with the IAM user
# below (bucket + invalidation only), never by this repo's CI user.
# -----------------------------------------------------------------------------

locals {
  # Host the *app* is served from. Every backend URL that points a browser at
  # the app (FRONTEND_URL, Stripe return URLs, CORS) derives from this.
  app_host = var.marketing_live ? "app.${var.domain_name}" : var.domain_name

  marketing_aliases = var.domain_name != "" && var.marketing_live ? [var.domain_name, "www.${var.domain_name}"] : []
}

# ---- Bucket -----------------------------------------------------------------

resource "aws_s3_bucket" "marketing" {
  bucket = "${var.app_name}-marketing-${var.environment}"

  tags = { Name = "${var.app_name}-marketing" }
}

resource "aws_s3_bucket_public_access_block" "marketing" {
  bucket = aws_s3_bucket.marketing.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "marketing" {
  bucket = aws_s3_bucket.marketing.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "marketing" {
  name                              = "${var.app_name}-marketing-oac"
  description                       = "OAC for ${var.app_name} marketing bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ---- Viewer-request function ------------------------------------------------
# CloudFront allows one function per event type per behavior, so the www→apex
# redirect and the static-site index rewrite live in one function. Astro emits
# <route>/index.html and S3 does not resolve directory indexes.

resource "aws_cloudfront_function" "marketing_router" {
  name    = "${var.app_name}-marketing-router"
  runtime = "cloudfront-js-2.0"
  comment = "www -> apex 301, then append index.html for directory routes"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      var host = request.headers.host && request.headers.host.value;
      if (host === 'www.${var.domain_name}') {
        var qs = request.querystring && Object.keys(request.querystring).length
          ? '?' + Object.keys(request.querystring).map(function (k) {
              var v = request.querystring[k];
              return encodeURIComponent(k) + '=' + encodeURIComponent(v.value);
            }).join('&')
          : '';
        return {
          statusCode: 301,
          statusDescription: 'Moved Permanently',
          headers: {
            'location': { value: 'https://${var.domain_name}' + request.uri + qs }
          }
        };
      }
      var uri = request.uri;
      if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
      } else if (!uri.includes('.')) {
        request.uri = uri + '/index.html';
      }
      return request;
    }
  EOT
}

# ---- Distribution -----------------------------------------------------------

resource "aws_cloudfront_distribution" "marketing" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.app_name} marketing site"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  aliases = local.marketing_aliases

  origin {
    origin_id                = "s3-marketing"
    domain_name              = aws_s3_bucket.marketing.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.marketing.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-marketing"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = local.cf_cache_caching_optimized
    response_headers_policy_id = local.cf_response_security_headers

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.marketing_router.arn
    }
  }

  # A private bucket behind OAC answers 403, not 404, for a missing key, so
  # both map to the real 404 page with a real 404 status (no SPA fallback here).
  custom_error_response {
    error_code            = 403
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = length(local.marketing_aliases) == 0
    acm_certificate_arn            = length(local.marketing_aliases) > 0 ? aws_acm_certificate_validation.main[0].certificate_arn : null
    ssl_support_method             = length(local.marketing_aliases) > 0 ? "sni-only" : null
    minimum_protocol_version       = length(local.marketing_aliases) > 0 ? "TLSv1.2_2021" : "TLSv1"
  }

  tags = { Name = "${var.app_name}-marketing-cdn" }
}

data "aws_iam_policy_document" "marketing_bucket" {
  statement {
    sid       = "AllowCloudFrontOACRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.marketing.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.marketing.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "marketing" {
  bucket = aws_s3_bucket.marketing.id
  policy = data.aws_iam_policy_document.marketing_bucket.json
}

# ---- Deploy identity for the marketing repo's GitHub Actions ---------------

resource "aws_iam_user" "marketing_deploy" {
  name = "${var.app_name}-marketing-deploy"

  tags = { Name = "${var.app_name}-marketing-deploy" }
}

resource "aws_iam_access_key" "marketing_deploy" {
  user = aws_iam_user.marketing_deploy.name
}

data "aws_iam_policy_document" "marketing_deploy" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.marketing.arn]
  }

  statement {
    sid       = "SyncObjects"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.marketing.arn}/*"]
  }

  statement {
    sid       = "Invalidate"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.marketing.arn]
  }
}

resource "aws_iam_user_policy" "marketing_deploy" {
  name   = "${var.app_name}-marketing-deploy"
  user   = aws_iam_user.marketing_deploy.name
  policy = data.aws_iam_policy_document.marketing_deploy.json
}

# ---- Outputs (feed the marketing repo's GitHub secrets/variables) ----------

output "marketing_bucket" {
  description = "S3 bucket for the marketing site (GitHub variable MARKETING_BUCKET)"
  value       = aws_s3_bucket.marketing.bucket
}

output "marketing_distribution_id" {
  description = "CloudFront distribution for the marketing site (GitHub variable MARKETING_DISTRIBUTION_ID)"
  value       = aws_cloudfront_distribution.marketing.id
}

output "marketing_cloudfront_domain_name" {
  description = "Hostname to verify the marketing site on before cutover"
  value       = aws_cloudfront_distribution.marketing.domain_name
}

output "marketing_deploy_access_key_id" {
  description = "GitHub secret AWS_ACCESS_KEY_ID for the marketing repo"
  value       = aws_iam_access_key.marketing_deploy.id
  sensitive   = true
}

output "marketing_deploy_secret_access_key" {
  description = "GitHub secret AWS_SECRET_ACCESS_KEY for the marketing repo"
  value       = aws_iam_access_key.marketing_deploy.secret
  sensitive   = true
}
