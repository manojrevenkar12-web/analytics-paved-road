# The lake on AWS: an S3 bucket for Iceberg data, Glue databases as the Iceberg catalog (served
# through Glue's Iceberg REST endpoint), a KMS key, and least-privilege roles for the gateway
# (read) and the pipelines (write) on an existing EKS cluster (IRSA).
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "analytics-paved-road", owner = "data-platform" } }
}

data "aws_caller_identity" "me" {}

resource "aws_kms_key" "lake" {
  description             = "Lake data and catalog metadata"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_s3_bucket" "lake" {
  bucket = "${var.prefix}-lake-${data.aws_caller_identity.me.account_id}"
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = var.noncurrent_days }
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
}

resource "aws_glue_catalog_database" "layer" {
  for_each     = toset(["raw", "mart"])
  name         = each.key
  location_uri = "s3://${aws_s3_bucket.lake.bucket}/${each.key}/"
}

data "aws_iam_policy_document" "assume" {
  for_each = { gateway = "apr-gateway", pipelines = "apr-pipelines" }
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.eks_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(var.eks_oidc_provider_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.namespace}:${each.value}"]
    }
  }
}

resource "aws_iam_role" "app" {
  for_each           = data.aws_iam_policy_document.assume
  name               = "${var.prefix}-${each.key}"
  assume_role_policy = each.value.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "ReadLake"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    sid       = "ReadCatalog"
    actions   = ["glue:GetCatalog", "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables"]
    resources = ["*"]
  }
  statement {
    sid       = "Decrypt"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.lake.arn]
  }
}

data "aws_iam_policy_document" "pipelines" {
  source_policy_documents = [data.aws_iam_policy_document.gateway.json]
  statement {
    sid       = "WriteLake"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    sid       = "WriteCatalog"
    actions   = ["glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable"]
    resources = ["*"]
  }
  statement {
    sid       = "Encrypt"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "gateway" {
  role   = aws_iam_role.app["gateway"].id
  policy = data.aws_iam_policy_document.gateway.json
}

resource "aws_iam_role_policy" "pipelines" {
  role   = aws_iam_role.app["pipelines"].id
  policy = data.aws_iam_policy_document.pipelines.json
}
