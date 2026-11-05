# ============================================================================
# Wait for IAM propagation before triggering builds
# ============================================================================

resource "time_sleep" "wait_for_iam" {
  depends_on = [
    aws_iam_role_policy.codebuild,
    aws_iam_role_policy.orchestrator_execution,
    aws_iam_role_policy.specialist_execution
  ]

  create_duration = "30s"
}

# ============================================================================
# Trigger CodeBuild - Sequential Build Process
# Specialist builds first (independent), then Orchestrator (depends on Specialist)
# ============================================================================

# Trigger Specialist Build (Independent - Builds First)
resource "null_resource" "trigger_build_specialist" {
  triggers = {
    build_project   = aws_codebuild_project.specialist_image.id
    image_tag       = var.image_tag
    ecr_repository  = aws_ecr_repository.specialist.id
    source_code_md5 = data.archive_file.specialist_source.output_md5
  }

  provisioner "local-exec" {
    # IMAGE_TAG is passed through the environment, not interpolated into
    # the command string, so a value like "latest$(cmd)" can't be
    # re-parsed as shell syntax by the /bin/sh -c this provisioner runs
    # under (double-quoting alone only blocks ;/&&/|, not $()/backticks).
    command = "${path.module}/scripts/build-image.sh \"${aws_codebuild_project.specialist_image.name}\" \"${data.aws_region.current.id}\" \"${aws_ecr_repository.specialist.name}\" \"$IMAGE_TAG\" \"${aws_ecr_repository.specialist.repository_url}\""

    environment = {
      IMAGE_TAG = var.image_tag
    }
  }

  depends_on = [
    aws_codebuild_project.specialist_image,
    aws_ecr_repository.specialist,
    aws_iam_role_policy.codebuild,
    aws_s3_object.specialist_source,
    time_sleep.wait_for_iam
  ]
}

# Trigger Orchestrator Build (Depends on Specialist Build Completion)
resource "null_resource" "trigger_build_orchestrator" {
  triggers = {
    build_project   = aws_codebuild_project.orchestrator_image.id
    image_tag       = var.image_tag
    ecr_repository  = aws_ecr_repository.orchestrator.id
    source_code_md5 = data.archive_file.orchestrator_source.output_md5
    # Also rebuild if Specialist build changes
    specialist_build = null_resource.trigger_build_specialist.id
  }

  provisioner "local-exec" {
    # IMAGE_TAG is passed through the environment, not interpolated into
    # the command string, so a value like "latest$(cmd)" can't be
    # re-parsed as shell syntax by the /bin/sh -c this provisioner runs
    # under (double-quoting alone only blocks ;/&&/|, not $()/backticks).
    command = "${path.module}/scripts/build-image.sh \"${aws_codebuild_project.orchestrator_image.name}\" \"${data.aws_region.current.id}\" \"${aws_ecr_repository.orchestrator.name}\" \"$IMAGE_TAG\" \"${aws_ecr_repository.orchestrator.repository_url}\""

    environment = {
      IMAGE_TAG = var.image_tag
    }
  }

  depends_on = [
    aws_codebuild_project.orchestrator_image,
    aws_ecr_repository.orchestrator,
    aws_iam_role_policy.codebuild,
    aws_s3_object.orchestrator_source,
    null_resource.trigger_build_specialist, # CRITICAL: Wait for Specialist build
    time_sleep.wait_for_iam
  ]
}
