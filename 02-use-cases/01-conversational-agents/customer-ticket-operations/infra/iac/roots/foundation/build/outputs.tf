locals {
  outputs = {
    artifacts_bucket = aws_s3_bucket.artifacts.bucket
    codebuild        = aws_codebuild_project.build.name
    ecr_repository   = aws_ecr_repository.agent.repository_url
  }
}

resource "aws_ssm_parameter" "build" {
  for_each = local.outputs
  name     = "${local.ssm}/build/${each.key}"
  type     = "String"
  value    = each.value
}

output "build" {
  value = local.outputs
}
