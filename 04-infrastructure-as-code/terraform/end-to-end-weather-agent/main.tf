# ============================================================================
# Wait for IAM propagation before triggering build
# ============================================================================

resource "time_sleep" "wait_for_iam" {
  depends_on = [
    aws_iam_role_policy.codebuild,
    aws_iam_role_policy.agent_execution
  ]

  create_duration = "30s"
}

# ============================================================================
# Trigger CodeBuild - Build Docker Image
# ============================================================================

resource "null_resource" "trigger_build" {
  triggers = {
    build_project   = aws_codebuild_project.agent_image.id
    image_tag       = var.image_tag
    ecr_repository  = aws_ecr_repository.weather_ecr.id
    source_code_md5 = data.archive_file.agent_source.output_md5
  }

  provisioner "local-exec" {
    # IMAGE_TAG is passed through the environment, not interpolated into
    # the command string, so a value like "latest$(cmd)" can't be
    # re-parsed as shell syntax by the /bin/sh -c this provisioner runs
    # under (double-quoting alone only blocks ;/&&/|, not $()/backticks).
    command = "${path.module}/scripts/build-image.sh \"${aws_codebuild_project.agent_image.name}\" \"${data.aws_region.current.id}\" \"${aws_ecr_repository.weather_ecr.name}\" \"$IMAGE_TAG\" \"${aws_ecr_repository.weather_ecr.repository_url}\""

    environment = {
      IMAGE_TAG = var.image_tag
    }
  }

  depends_on = [
    aws_codebuild_project.agent_image,
    aws_ecr_repository.weather_ecr,
    aws_iam_role_policy.codebuild,
    aws_s3_object.agent_source,
    time_sleep.wait_for_iam
  ]
}

# ============================================================================
# Weather Agent Runtime
# ============================================================================

resource "aws_bedrockagentcore_agent_runtime" "weather_agent" {
  agent_runtime_name = "${replace(var.stack_name, "-", "_")}_${var.agent_name}"
  description        = "Weather agent runtime for ${var.stack_name}"
  role_arn           = aws_iam_role.agent_execution.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.weather_ecr.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = var.network_mode
  }

  environment_variables = {
    AWS_REGION          = data.aws_region.current.id
    AWS_DEFAULT_REGION  = data.aws_region.current.id
    RESULTS_BUCKET      = aws_s3_bucket.results.id
    BROWSER_ID          = aws_bedrockagentcore_browser.browser.browser_id
    CODE_INTERPRETER_ID = aws_bedrockagentcore_code_interpreter.code_interpreter.code_interpreter_id
    MEMORY_ID           = aws_bedrockagentcore_memory.memory.id
  }

  tags = {
    Name        = "${var.stack_name}-agent-runtime"
    Environment = "production"
    Module      = "BedrockAgentCore"
    Agent       = "WeatherAgent"
  }

  depends_on = [
    null_resource.trigger_build,
    aws_iam_role_policy.agent_execution,
    aws_iam_role_policy_attachment.agent_execution_managed,
    aws_bedrockagentcore_browser.browser,
    aws_bedrockagentcore_code_interpreter.code_interpreter,
    aws_bedrockagentcore_memory.memory
  ]
}
