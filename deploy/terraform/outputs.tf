output "bucket" { value = aws_s3_bucket.lake.bucket }
output "iceberg_rest_uri" {
  value       = "https://glue.${var.region}.amazonaws.com/iceberg"
  description = "APR_ICEBERG_REST_URI (SigV4, signing name glue)"
}
output "gateway_role_arn" { value = aws_iam_role.app["gateway"].arn }
output "pipelines_role_arn" { value = aws_iam_role.app["pipelines"].arn }
