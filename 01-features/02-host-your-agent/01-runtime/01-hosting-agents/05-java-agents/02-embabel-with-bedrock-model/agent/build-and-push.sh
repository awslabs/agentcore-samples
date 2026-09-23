#!/bin/bash

#######################################################################
# build-and-push.sh
#
# Build Docker image and push to Amazon ECR
#
# This script builds the Spring Boot agent application as a Docker
# image and pushes it to Amazon ECR.
#######################################################################

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

#######################################################################
# Usage
#######################################################################
usage() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Build Docker image and push to Amazon ECR.

Required:
  -r, --region          AWS region (e.g., us-east-1)
  -u, --ecr-uri         ECR repository URI (e.g., 123456789012.dkr.ecr.us-east-1.amazonaws.com/my-repo)

Optional:
  -t, --tag             Image tag (default: latest)
  -b, --build-only      Build image only, do not push to ECR
  -h, --help            Show this help message

Examples:
  # Build and push with default tag 'latest'
  $(basename "$0") -r us-east-1 -u 123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent

  # Build and push with custom tag
  $(basename "$0") -r us-east-1 -u 123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent -t v1.0.0

  # Build only (no push)
  $(basename "$0") -r us-east-1 -u 123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent -b

EOF
    exit 1
}

#######################################################################
# Logging functions
#######################################################################
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

#######################################################################
# Parse arguments
#######################################################################
AWS_REGION=""
ECR_URI=""
IMAGE_TAG="latest"
BUILD_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--region)
            AWS_REGION="$2"
            shift 2
            ;;
        -u|--ecr-uri)
            ECR_URI="$2"
            shift 2
            ;;
        -t|--tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        -b|--build-only)
            BUILD_ONLY=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

#######################################################################
# Validate required arguments
#######################################################################
if [[ -z "$AWS_REGION" ]]; then
    log_error "AWS region is required (-r, --region)"
    usage
fi

if [[ -z "$ECR_URI" ]]; then
    log_error "ECR repository URI is required (-u, --ecr-uri)"
    usage
fi

#######################################################################
# Validate prerequisites
#######################################################################
log_info "Validating prerequisites..."

# Check Docker is installed and running
if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker info &> /dev/null; then
    log_error "Docker daemon is not running. Please start Docker first."
    exit 1
fi

# Check AWS CLI is installed
if ! command -v aws &> /dev/null; then
    log_error "AWS CLI is not installed. Please install AWS CLI first."
    exit 1
fi

log_info "Prerequisites validated successfully"

#######################################################################
# Build Docker image
#######################################################################
FULL_IMAGE_URI="${ECR_URI}:${IMAGE_TAG}"

log_info "Building Docker image..."
log_info "  Image: $FULL_IMAGE_URI"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker build \
    --tag "$FULL_IMAGE_URI" \
    "$SCRIPT_DIR"

log_info "Docker image built successfully"

#######################################################################
# Push to ECR (unless build-only mode)
#######################################################################
if [[ "$BUILD_ONLY" == true ]]; then
    log_info "Build-only mode: Skipping ECR push"
    log_info "Local image available as: $FULL_IMAGE_URI"
    exit 0
fi

log_info "Logging into Amazon ECR..."

# Extract ECR registry from URI (everything before the first /)
ECR_REGISTRY="${ECR_URI%%/*}"

# Login to ECR
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ECR_REGISTRY"

log_info "ECR login successful"

log_info "Pushing image to ECR..."
log_info "  Destination: $FULL_IMAGE_URI"

docker push "$FULL_IMAGE_URI"

log_info "Image pushed successfully to ECR"

#######################################################################
# Summary
#######################################################################
echo ""
log_info "=========================================="
log_info "Build and push completed successfully!"
log_info "=========================================="
log_info "Image URI: $FULL_IMAGE_URI"
log_info "Region: $AWS_REGION"
echo ""
