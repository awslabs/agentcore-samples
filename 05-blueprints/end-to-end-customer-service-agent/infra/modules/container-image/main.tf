data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  image_src_path = "${path.root}/${var.relative_image_src_path}"
  image_src_hash = sha512(
    join(
      "",
      # TODO: Find a way to exclude .venv, dist, and potentially other subfolders:
      [for f in fileset(".", "${local.image_src_path}/**") : filesha512(f)]
    )
  )

  image_build_extra_args = "--platform linux/arm64"
  # IMAGE_BUILD_TOOL/IMAGE_TAG are passed through the local-exec
  # provisioner's environment below, not interpolated into this command
  # string, so a value like "latest$(cmd)" can't be re-parsed as shell
  # syntax by the /bin/sh -c this provisioner runs under (and unlike the
  # original, unquoted interpolation, quoting here also blocks ;/&&/|).
  image_build_push_cmd = <<-EOT
    aws ecr get-login-password | "$IMAGE_BUILD_TOOL" login --username AWS \
      --password-stdin "${aws_ecr_repository.ecr_repository.repository_url}" &&
    "$IMAGE_BUILD_TOOL" build ${local.image_build_extra_args} \
      -t "${aws_ecr_repository.ecr_repository.repository_url}:$IMAGE_TAG" \
      ${local.image_src_path} &&
    "$IMAGE_BUILD_TOOL" push "${aws_ecr_repository.ecr_repository.repository_url}:$IMAGE_TAG"
  EOT
}

resource "aws_ecr_repository" "ecr_repository" {
  name                 = var.repository_name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
  }
}

resource "terraform_data" "ecr_image" {
  triggers_replace = [
    aws_ecr_repository.ecr_repository.id,
    var.force_image_rebuild == true ? timestamp() : local.image_src_hash
  ]

  input = "${aws_ecr_repository.ecr_repository.repository_url}:${var.image_tag}"

  provisioner "local-exec" {
    command = local.image_build_push_cmd

    environment = {
      IMAGE_BUILD_TOOL = var.image_build_tool
      IMAGE_TAG        = var.image_tag
    }
  }
}
