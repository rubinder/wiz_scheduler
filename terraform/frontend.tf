# -----------------------------------------------------------------------------
# Frontend hosting — S3 (private) + CloudFront (OAC) with ALB origin for /api/*
#
# Browser always talks to CloudFront. Default behavior serves the SPA from S3;
# /api/* is forwarded to the ALB with caching disabled and all headers/cookies
# forwarded (so JWT auth + NDJSON streaming work transparently).
#
# NOTE: ACM cert for CloudFront MUST be in us-east-1. The existing
#       aws_acm_certificate.main is created in var.aws_region. If you ever
#       change aws_region away from us-east-1, add a us-east-1 provider alias
#       and a second cert for CloudFront.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# S3 bucket for built SPA assets (private — only CloudFront OAC can read)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.app_name}-frontend-${var.environment}"

  tags = { Name = "${var.app_name}-frontend" }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}

# -----------------------------------------------------------------------------
# CloudFront Origin Access Control (OAC) — modern replacement for OAI
# -----------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.app_name}-frontend-oac"
  description                       = "OAC for ${var.app_name} frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# -----------------------------------------------------------------------------
# CloudFront Function — 301 redirect www.<domain> → <domain>
# Runs on viewer-request, sub-millisecond, ~$0.10 per million invocations.
# Only attached when a domain is configured.
# -----------------------------------------------------------------------------

resource "aws_cloudfront_function" "www_to_apex" {
  count   = var.domain_name != "" ? 1 : 0
  name    = "${var.app_name}-www-to-apex"
  runtime = "cloudfront-js-2.0"
  comment = "301 redirect www.${var.domain_name} to ${var.domain_name}"
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
      return request;
    }
  EOT
}

# -----------------------------------------------------------------------------
# CloudFront distribution — S3 default + ALB for /api/*
# -----------------------------------------------------------------------------

locals {
  # CloudFront managed policy IDs (static, well-known)
  cf_cache_caching_optimized      = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  cf_cache_caching_disabled       = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  cf_origin_req_all_viewer        = "216adef6-5c7f-47e4-b989-5492eafa07d3"
  cf_origin_req_all_viewer_exhost = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # AllViewerExceptHostHeader
  cf_response_security_headers    = "67f7725c-6f97-4210-82d7-5512b31e9d03" # SecurityHeadersPolicy
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.app_name} frontend + API proxy"
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # US, Canada, Europe — cheapest tier

  aliases = var.domain_name != "" ? [var.domain_name, "www.${var.domain_name}"] : []

  # ---- S3 origin (static assets) ----
  origin {
    origin_id                = "s3-frontend"
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  # ---- ALB origin (API) ----
  # When a domain is configured, CloudFront talks to the ALB via
  # alb.<domain> (covered by the *.<domain> SAN) so the origin TLS
  # certificate's hostname matches. Without a domain, fall back to the
  # ALB's default DNS name over plain HTTP.
  origin {
    origin_id   = "alb-api"
    domain_name = var.domain_name != "" ? "alb.${var.domain_name}" : aws_lb.main.dns_name

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = var.domain_name != "" ? "https-only" : "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # ---- Default behavior — serve SPA from S3 ----
  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = local.cf_cache_caching_optimized
    response_headers_policy_id = local.cf_response_security_headers

    dynamic "function_association" {
      for_each = var.domain_name != "" ? [1] : []
      content {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.www_to_apex[0].arn
      }
    }
  }

  # ---- /api/* — forward to ALB, no caching, forward everything ----
  ordered_cache_behavior {
    path_pattern           = "/api/*"
    target_origin_id       = "alb-api"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id          = local.cf_cache_caching_disabled
    origin_request_policy_id = local.cf_origin_req_all_viewer_exhost

    dynamic "function_association" {
      for_each = var.domain_name != "" ? [1] : []
      content {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.www_to_apex[0].arn
      }
    }
  }

  # ---- SPA routing — serve index.html on 403/404 from S3 ----
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.domain_name == ""
    acm_certificate_arn            = var.domain_name != "" ? aws_acm_certificate_validation.main[0].certificate_arn : null
    ssl_support_method             = var.domain_name != "" ? "sni-only" : null
    minimum_protocol_version       = var.domain_name != "" ? "TLSv1.2_2021" : "TLSv1"
  }

  tags = { Name = "${var.app_name}-frontend-cdn" }
}

# -----------------------------------------------------------------------------
# S3 bucket policy — allow CloudFront OAC to read
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "AllowCloudFrontOACRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket.json
}
