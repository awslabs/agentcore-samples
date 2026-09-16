#!/usr/bin/env bash
# Build the base-agent image at a given size and push it to Amazon ECR.
#
# Usage: ./build-and-push.sh
#        IMAGE_SIZE=750mb ./build-and-push.sh
#
# IMAGE_SIZE  default: 200mb  (200mb | 500mb | 750mb | 1gb | 2gb)
#   Selects how much incompressible padding (infrastructure/../base-agent's
#   Dockerfile ARG PAD_MB) is added on top of today's unpadded image, and is
#   used as the image tag so all five sizes coexist in the same ECR repo.
#   200mb adds no padding — it is today's image, unchanged.
#
#   "200mb" is a label, not a literal measurement: the unpadded image (plain
#   python:3.13-slim + fastapi/uvicorn/pydantic, pip removed after install)
#   measured ~227 MB locally (`docker images`, linux/arm64) after trimming
#   away the heavier uv/bookworm-slim base the original benchmark used
#   (~318 MB) — see base-agent/Dockerfile and pyproject.toml for what changed.
#   Getting closer to a literal 200 MB would mean an Alpine/musl base, which
#   risks missing prebuilt arm64 wheels for pydantic-core and was not done
#   without checking first.
#
#   The MB-to-pad mapping below is computed against that measured ~227 MB
#   floor, not an absolute total. Local Docker Desktop size reporting has
#   shown real inconsistency across tools (`docker images` vs `docker
#   history` vs `du` inside the container gave three different numbers on the
#   same image here) — treat these as approximate, and prefer the size ECR
#   actually reports after a real push (`aws ecr describe-images --query
#   'imageDetails[].imageSizeInBytes'`) over any local measurement if the two
#   disagree.
set -euo pipefail

# Captured BEFORE common.sh runs: common.sh itself defaults IMAGE_TAG to
# "latest", so by the time this script would otherwise write
# `IMAGE_TAG="${IMAGE_TAG:-${IMAGE_SIZE}}"`, the variable is no longer empty
# and that fallback never fires — every size silently pushes to :latest,
# overwriting the same tag. Save what the caller actually passed (if
# anything) first, and use THAT as the fallback check below.
USER_IMAGE_TAG="${IMAGE_TAG:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require aws
require docker

IMAGE_SIZE="${IMAGE_SIZE:-200mb}"
case "${IMAGE_SIZE}" in
  200mb) PAD_MB=0 ;;      # baseline, unpadded (~227 MB measured)
  500mb) PAD_MB=273 ;;
  750mb) PAD_MB=523 ;;
  1gb)   PAD_MB=797 ;;
  2gb)   PAD_MB=1821 ;;
  *) die "unknown IMAGE_SIZE '${IMAGE_SIZE}' (expected 200mb|500mb|750mb|1gb|2gb)" ;;
esac
IMAGE_TAG="${USER_IMAGE_TAG:-${IMAGE_SIZE}}"

REGISTRY="$(ecr_registry)"
URI="$(image_uri)"

log "Platform:   ${IMAGE_PLATFORM}"
log "Image size: ${IMAGE_SIZE} (PAD_MB=${PAD_MB})"
log "Image URI:  ${URI}"

# Ensure the ECR repository exists (idempotent).
if ! aws ecr describe-repositories \
      --repository-names "${ECR_REPOSITORY}" \
      --region "${AWS_REGION}" >/dev/null 2>&1; then
  log "Creating ECR repository '${ECR_REPOSITORY}'..."
  aws ecr create-repository \
    --repository-name "${ECR_REPOSITORY}" \
    --region "${AWS_REGION}" >/dev/null
fi

# Authenticate Docker to ECR.
log "Logging in to ECR (${REGISTRY})..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

# Ensure a buildx builder exists for cross-platform builds.
if ! docker buildx inspect infra-builder >/dev/null 2>&1; then
  log "Creating buildx builder 'infra-builder'..."
  docker buildx create --name infra-builder --use >/dev/null
else
  docker buildx use infra-builder
fi

# Build for the target platform and push to ECR.
log "Building and pushing ${URI}..."
docker buildx build \
  --platform "${IMAGE_PLATFORM}" \
  --build-arg "PAD_MB=${PAD_MB}" \
  --tag "${URI}" \
  --push \
  "${BUILD_CONTEXT}"

log "Done. Pushed ${URI}"
echo "${URI}"
