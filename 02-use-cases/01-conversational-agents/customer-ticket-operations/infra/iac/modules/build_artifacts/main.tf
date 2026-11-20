# Reads the manifest of the current remote build (written by CodeBuild, see infra/codebuild/).
# Roots deploy Lambdas and web files straight from these S3 artifacts; nothing is packaged locally.

variable "project" {
  type = string
}

variable "environment" {
  type = string
}

data "aws_ssm_parameter" "bucket" {
  name = "/${var.project}/${var.environment}/build/artifacts_bucket"
}

data "aws_ssm_parameter" "current" {
  name = "/${var.project}/${var.environment}/build/current"
}

data "aws_s3_object" "manifest" {
  bucket = data.aws_ssm_parameter.bucket.value
  key    = "builds/${data.aws_ssm_parameter.current.value}/manifest.json"
}

locals {
  manifest = jsondecode(data.aws_s3_object.manifest.body)
}

output "bucket" {
  value = data.aws_ssm_parameter.bucket.value
}

output "build_id" {
  value = local.manifest.build_id
}

# name => { key, hash }  (hash is base64 SHA-256, i.e. Terraform's source_code_hash)
output "lambda" {
  value = local.manifest.lambda
}

# [{ path, key, content_type }]
output "web" {
  value = local.manifest.web
}

# name => image URI pinned by digest (repo@sha256:...), e.g. images["agent"]
output "images" {
  value = try(local.manifest.images, {})
}
