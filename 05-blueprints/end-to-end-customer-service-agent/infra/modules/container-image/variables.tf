variable "force_image_rebuild" {
  description = "Set true to force rebuild & push of image to ECR even if source appears unchanged"
  default     = false
  type        = bool
}

variable "image_build_tool" {
  description = "Either 'docker' or a Docker-compatible alternative e.g. 'finch'"
  default     = "docker"
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9._/-]+$", var.image_build_tool))
    error_message = "image_build_tool must be a bare command name or path: alphanumeric characters, dots, underscores, hyphens, and slashes only."
  }
}

variable "relative_image_src_path" {
  description = "Path to container image source folder, relative to Terraform root"
  default     = "../cx-agent-backend"
  type        = string
}

variable "image_tag" {
  description = "Tag to apply to the pushed container image in Amazon ECR"
  default     = "latest"
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9._-]+$", var.image_tag))
    error_message = "Image tag must contain only alphanumeric characters, dots, underscores, and hyphens."
  }
}

variable "repository_name" {
  description = "Name of the Amazon ECR repository to create and deploy the image to"
  type        = string
}
