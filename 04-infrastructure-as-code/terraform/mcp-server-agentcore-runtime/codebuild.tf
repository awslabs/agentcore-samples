# ============================================================================
# CodeBuild Project - Build and Push MCP Server Docker Image
# ============================================================================

resource "aws_codebuild_project" "agent_image" {
  name          = "${var.stack_name}-mcp-server-build"
  description   = "Build MCP server Docker image for ${var.stack_name}"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 60

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_LARGE"
    image                       = "aws/codebuild/amazonlinux2-aarch64-standard:3.0"
    type                        = "ARM_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = data.aws_region.current.id
    }

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.id
    }

    environment_variable {
      name  = "IMAGE_REPO_NAME"
      value = aws_ecr_repository.server_ecr.name
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag
    }

    environment_variable {
      name  = "STACK_NAME"
      value = var.stack_name
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.agent_source.id}/${aws_s3_object.agent_source.key}"
    buildspec = file("${path.module}/buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/aws/codebuild/${var.stack_name}-mcp-server-build"
    }
  }

  tags = {
    Name   = "${var.stack_name}-mcp-server-build"
    Module = "CodeBuild"
  }
}

# ============================================================================
# Trigger CodeBuild - Build Image Before Creating Runtime
# ============================================================================

resource "null_resource" "trigger_build" {
  triggers = {
    build_project = aws_codebuild_project.agent_image.id
    image_tag     = var.image_tag
    # Trigger rebuild if ECR repository changes
    ecr_repository = aws_ecr_repository.server_ecr.id
    # Trigger rebuild when source code changes (MD5 hash)
    source_code_md5 = data.archive_file.agent_source.output_md5
  }

  provisioner "local-exec" {
    # IMAGE_TAG is passed through the environment, not interpolated into
    # the command string, so a value like "latest$(cmd)" can't be
    # re-parsed as shell syntax by the /bin/sh -c this provisioner runs
    # under (double-quoting alone only blocks ;/&&/|, not $()/backticks).
    command = "${path.module}/scripts/build-image.sh \"${aws_codebuild_project.agent_image.name}\" \"${data.aws_region.current.id}\" \"${aws_ecr_repository.server_ecr.name}\" \"$IMAGE_TAG\" \"${aws_ecr_repository.server_ecr.repository_url}\""

    environment = {
      IMAGE_TAG = var.image_tag
    }
  }

  depends_on = [
    aws_codebuild_project.agent_image,
    aws_ecr_repository.server_ecr,
    aws_iam_role_policy.codebuild,
    aws_s3_object.agent_source
  ]
}
