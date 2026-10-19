# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# ECR repository + build-and-push
#
# Both container images in this stack (the agent runtime and the console API)
# need the same three things: a repository, a lifecycle policy, and a
# build/push step that runs during apply. Duplicating that across runtime.tf and
# console_api.tf let the two copies drift — only one of them carried the
# --provenance/--sbom flags that Lambda requires, which cost a debugging cycle.
#
# Everything here is ARM64: the agent runtime requires it, and matching it for
# the API keeps one build path and cuts Lambda cost.
# =============================================================================

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.35.1"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2.0"
    }
  }
}

resource "aws_ecr_repository" "this" {
  name                 = var.repository_name
  image_tag_mutability = "MUTABLE"
  # Demo stack: allow `terraform destroy` to remove the repo with images in it.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain only the ${var.retained_image_count} most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.retained_image_count
      }
      action = { type = "expire" }
    }]
  })
}

# Content hash over the build context. Exposed as an output so callers can hang
# `replace_triggered_by` off it, forcing the consuming resource to be replaced
# when the image content changes.
resource "terraform_data" "image_hash" {
  input = sha256(join("", [for file in var.source_files : filesha256(file)]))
}

resource "null_resource" "build_push" {
  triggers = {
    image_hash = terraform_data.image_hash.output
    image_uri  = local.image_uri
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      REGION="${var.region}"
      ACCOUNT="${var.account_id}"
      # Interpolate the full tags in Terraform rather than composing them in the
      # shell: under zsh, "$REPO:latest" is parsed as a ':l' history modifier
      # and silently produces a mangled tag.
      IMAGE="${local.image_uri}"
      LATEST="${aws_ecr_repository.this.repository_url}:latest"

      # Docker or Finch — both expose a Docker-compatible CLI, so the only
      # per-engine difference below is which buildx-only flags the builder
      # accepts (handled by feature detection).
      CLI="${var.container_cli}"

      if ! "$CLI" info >/dev/null 2>&1; then
        if [ "$CLI" = "finch" ]; then
          echo "ERROR: Finch is not running. Run 'finch vm start' and re-apply." >&2
        else
          echo "ERROR: Docker is not running. Start Docker Desktop and re-apply." >&2
        fi
        exit 1
      fi

      # Both images build in parallel, and two concurrent logins race the macOS
      # keychain credential store ("item already exists in the keychain
      # (-25299)"). An already-valid login makes this a no-op, so treat a login
      # failure as non-fatal: if the push below can't authenticate it will fail
      # loudly there.
      echo "Logging in to ECR with $CLI..."
      aws ecr get-login-password --region "$REGION" \
        | "$CLI" login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com" \
        || echo "  $CLI login returned nonzero (likely a concurrent-login keychain race); continuing"

      # --provenance/--sbom must be off on Docker buildx: it otherwise emits an
      # OCI image index with attestation manifests, which Lambda rejects with
      # "image manifest ... media type ... is not supported". Those flags are
      # buildx-specific — Finch's builder (nerdctl/BuildKit) does not accept
      # them and produces a plain manifest anyway — so pass each flag only if
      # this CLI's `build --help` advertises it. Keeps one build path for both
      # engines without hardcoding buildx assumptions.
      BUILD_FLAGS=(--platform linux/arm64)
      BUILD_HELP="$("$CLI" build --help 2>&1 || true)"
      case "$BUILD_HELP" in *--provenance*) BUILD_FLAGS+=(--provenance=false);; esac
      case "$BUILD_HELP" in *--sbom*)       BUILD_FLAGS+=(--sbom=false);;       esac

      # Tag with both the content hash (the URI the Lambda/runtime consumes) and
      # :latest (a human-readable pointer).
      echo "Building ARM64 image from ${var.build_context} with $CLI..."
      "$CLI" build \
        "$${BUILD_FLAGS[@]}" \
        -t "$IMAGE" \
        -t "$LATEST" \
        "${var.build_context}"

      echo "Pushing image..."
      "$CLI" push "$IMAGE"
      "$CLI" push "$LATEST"
      echo "SUCCESS: $IMAGE"
    EOT
  }

  depends_on = [aws_ecr_repository.this, aws_ecr_lifecycle_policy.this]
}
