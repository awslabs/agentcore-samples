# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# State migrations
#
# The ECR repository, lifecycle policy, and build/push resources were extracted
# from runtime.tf and console_api.tf into the shared ./modules/ecr-image module.
# Because that changes their state addresses, Terraform would otherwise plan to
# destroy and recreate them — which would delete the live repositories and their
# images. These blocks migrate the existing state to the new addresses instead,
# so the refactor is a no-op against real infrastructure.
#
# Safe to delete once every environment has applied this once.
# =============================================================================

moved {
  from = aws_ecr_repository.agent
  to   = module.agent_image.aws_ecr_repository.this
}

moved {
  from = aws_ecr_lifecycle_policy.agent
  to   = module.agent_image.aws_ecr_lifecycle_policy.this
}

moved {
  from = null_resource.agent_image
  to   = module.agent_image.null_resource.build_push
}

moved {
  from = aws_ecr_repository.console_api
  to   = module.console_api_image.aws_ecr_repository.this
}

moved {
  from = aws_ecr_lifecycle_policy.console_api
  to   = module.console_api_image.aws_ecr_lifecycle_policy.this
}

moved {
  from = null_resource.console_api_image
  to   = module.console_api_image.null_resource.build_push
}
