variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "prefix" {
  type    = string
  default = "apr"
}

variable "namespace" {
  type    = string
  default = "analytics"
}

variable "eks_oidc_provider_arn" {
  type        = string
  description = "IAM OIDC provider of the EKS cluster the gateway and pipelines run on"
}

variable "eks_oidc_provider_url" {
  type = string
}

variable "noncurrent_days" {
  type        = number
  default     = 30
  description = "How long overwritten object versions are kept (beyond Iceberg's own snapshot expiry)"
}
