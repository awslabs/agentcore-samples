# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  # Tag by content hash, not a floating :latest. A stable-per-content tag means
  # the image_uri only changes when the build context changes — which lets a
  # consuming Lambda update in place (an image_uri change is an in-place
  # update-function-code) instead of being replaced. Replacing an image Lambda
  # would tear down its Function URL, whose hostname is regenerated, breaking
  # the deployed frontend config. The build also pushes :latest for humans.
  image_tag = substr(terraform_data.image_hash.output, 0, 12)
  image_uri = "${aws_ecr_repository.this.repository_url}:${local.image_tag}"
}

output "image_uri" {
  description = "Content-addressed image URI (tag = first 12 chars of the build-context hash). Changes only when the build context changes, enabling in-place Lambda updates."
  value       = local.image_uri
}

output "repository_arn" {
  description = "Repository ARN, for scoping ecr:* permissions on the consumer's execution role."
  value       = aws_ecr_repository.this.arn
}

output "repository_url" {
  description = "Repository URL without a tag."
  value       = aws_ecr_repository.this.repository_url
}

output "image_hash" {
  description = "Content hash of the build context. Reference this from the consuming resource's `replace_triggered_by` so it is replaced when the image changes."
  value       = terraform_data.image_hash.output
}

output "build_id" {
  description = "ID of the build/push resource. Depend on this to order resource creation after the image exists in ECR."
  value       = null_resource.build_push.id
}
