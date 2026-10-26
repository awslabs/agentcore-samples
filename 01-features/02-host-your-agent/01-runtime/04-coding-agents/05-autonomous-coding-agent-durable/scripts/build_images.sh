#!/usr/bin/env bash
# scripts/build_images.sh — Build ARM64 container images using AWS CodeBuild.
#
# Zips source directories, uploads to S3, triggers CodeBuild projects, and waits
# for completion. No local Docker or QEMU required.
#
# Usage:
#   scripts/build_images.sh [coding-agent|sandbox|all]   (default: all)
#   scripts/build_images.sh all --tag v2                  (custom image tag)
#
# Prerequisites:
#   - AWS credentials configured (ada, aws sso, or env vars)
#   - CodeBuild projects deployed via CDK (cagent-build-coding-agent, cagent-build-sandbox)
#   - S3 bucket exists (created by CDK storage stack)
#
# Environment variables (override defaults):
#   AWS_REGION          Region (default: us-east-1)
#   PROJECT             Project prefix (default: cagent)
#   IMAGE_TAG           Image tag (default: latest)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- Defaults ----
AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT="${PROJECT:-cagent}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
TARGET="${1:-all}"

# Parse --tag flag
shift 2>/dev/null || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) IMAGE_TAG="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---- Derived values ----
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${PROJECT}-data-${AWS_ACCOUNT_ID}-${AWS_REGION}"
CB_PROJECT_CODING_AGENT="${PROJECT}-build-coding-agent"
CB_PROJECT_SANDBOX="${PROJECT}-build-sandbox"

# ---- Logging ----
log()  { printf '\033[1;34m[build]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[err]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- Validate credentials ----
aws sts get-caller-identity >/dev/null 2>&1 || die "No AWS credentials. Configure via ada, aws sso, or environment variables."
log "Account: $AWS_ACCOUNT_ID | Region: $AWS_REGION | Tag: $IMAGE_TAG"

# ---- Helper: zip and upload source ----
upload_source() {
    local name="$1" src_dir="$2"
    local zip_path="/tmp/${name}.zip"
    local s3_key="build-artifacts/${name}.zip"

    if [ ! -d "$ROOT_DIR/$src_dir" ]; then
        die "Source directory not found: $ROOT_DIR/$src_dir"
    fi

    # Copy shared module into the build context (Dockerfiles expect shared_libs/shared/)
    if [ -d "$ROOT_DIR/shared" ]; then
        rm -rf "$ROOT_DIR/$src_dir/shared_libs"
        mkdir -p "$ROOT_DIR/$src_dir/shared_libs"
        cp -r "$ROOT_DIR/shared" "$ROOT_DIR/$src_dir/shared_libs/shared"
    fi

    log "Zipping $src_dir -> $zip_path"
    (cd "$ROOT_DIR/$src_dir" && zip -qr "$zip_path" .)

    # Clean up copied shared module
    rm -rf "$ROOT_DIR/$src_dir/shared_libs"

    log "Uploading to s3://$BUCKET/$s3_key"
    aws s3 cp "$zip_path" "s3://$BUCKET/$s3_key" --region "$AWS_REGION" --quiet

    rm -f "$zip_path"
    ok "Source uploaded: s3://$BUCKET/$s3_key"
}

# ---- Helper: start build and wait ----
start_and_wait() {
    local project_name="$1" display_name="$2"

    log "Starting CodeBuild: $project_name"
    local build_id
    build_id=$(aws codebuild start-build \
        --project-name "$project_name" \
        --region "$AWS_REGION" \
        --environment-variables-override "name=IMAGE_TAG,value=$IMAGE_TAG,type=PLAINTEXT" \
        --query 'build.id' \
        --output text)

    log "Build started: $build_id"
    log "Waiting for $display_name build to complete..."

    # Poll for completion
    local status="IN_PROGRESS"
    local elapsed=0
    while [ "$status" = "IN_PROGRESS" ]; do
        sleep 15
        elapsed=$((elapsed + 15))
        status=$(aws codebuild batch-get-builds \
            --ids "$build_id" \
            --region "$AWS_REGION" \
            --query 'builds[0].buildStatus' \
            --output text)
        printf '\r  [%3ds] %s: %s' "$elapsed" "$display_name" "$status" >&2
    done
    echo >&2  # newline after progress

    if [ "$status" = "SUCCEEDED" ]; then
        ok "$display_name build succeeded (${elapsed}s)"
        local image_uri="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT}-${display_name}:${IMAGE_TAG}"
        ok "Image: $image_uri"
    else
        local phase_context
        phase_context=$(aws codebuild batch-get-builds \
            --ids "$build_id" \
            --region "$AWS_REGION" \
            --query 'builds[0].phases[?phaseStatus!=`SUCCEEDED`].{phase:phaseType,status:phaseStatus,context:contexts[0].message}' \
            --output table 2>/dev/null || true)
        die "$display_name build FAILED (status: $status). Build ID: $build_id\n$phase_context"
    fi
}

# ---- Main ----
case "$TARGET" in
    coding-agent)
        upload_source "coding-agent" "coding-agent"
        start_and_wait "$CB_PROJECT_CODING_AGENT" "coding-agent"
        ;;
    sandbox)
        upload_source "sandbox" "sandbox"
        start_and_wait "$CB_PROJECT_SANDBOX" "sandbox"
        ;;
    all)
        upload_source "coding-agent" "coding-agent"
        upload_source "sandbox" "sandbox"
        # Start both builds in parallel, then wait for each
        log "Starting both builds..."

        CA_BUILD_ID=$(aws codebuild start-build \
            --project-name "$CB_PROJECT_CODING_AGENT" \
            --region "$AWS_REGION" \
            --environment-variables-override "name=IMAGE_TAG,value=$IMAGE_TAG,type=PLAINTEXT" \
            --query 'build.id' \
            --output text)
        log "Coding agent build: $CA_BUILD_ID"

        SBX_BUILD_ID=$(aws codebuild start-build \
            --project-name "$CB_PROJECT_SANDBOX" \
            --region "$AWS_REGION" \
            --environment-variables-override "name=IMAGE_TAG,value=$IMAGE_TAG,type=PLAINTEXT" \
            --query 'build.id' \
            --output text)
        log "Sandbox build: $SBX_BUILD_ID"

        log "Waiting for both builds to complete..."

        # Wait for both
        ca_status="IN_PROGRESS"
        sbx_status="IN_PROGRESS"
        elapsed=0
        while [ "$ca_status" = "IN_PROGRESS" ] || [ "$sbx_status" = "IN_PROGRESS" ]; do
            sleep 15
            elapsed=$((elapsed + 15))

            if [ "$ca_status" = "IN_PROGRESS" ]; then
                ca_status=$(aws codebuild batch-get-builds \
                    --ids "$CA_BUILD_ID" \
                    --region "$AWS_REGION" \
                    --query 'builds[0].buildStatus' \
                    --output text)
            fi
            if [ "$sbx_status" = "IN_PROGRESS" ]; then
                sbx_status=$(aws codebuild batch-get-builds \
                    --ids "$SBX_BUILD_ID" \
                    --region "$AWS_REGION" \
                    --query 'builds[0].buildStatus' \
                    --output text)
            fi
            printf '\r  [%3ds] coding-agent: %-12s | sandbox: %-12s' "$elapsed" "$ca_status" "$sbx_status" >&2
        done
        echo >&2  # newline after progress

        # Report results
        failed=0
        if [ "$ca_status" = "SUCCEEDED" ]; then
            ok "coding-agent build succeeded (${elapsed}s)"
            ok "Image: ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT}-coding-agent:${IMAGE_TAG}"
        else
            warn "coding-agent build FAILED (status: $ca_status). Build ID: $CA_BUILD_ID"
            failed=1
        fi
        if [ "$sbx_status" = "SUCCEEDED" ]; then
            ok "sandbox build succeeded (${elapsed}s)"
            ok "Image: ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT}-sandbox:${IMAGE_TAG}"
        else
            warn "sandbox build FAILED (status: $sbx_status). Build ID: $SBX_BUILD_ID"
            failed=1
        fi

        [ "$failed" -eq 0 ] || die "One or more builds failed. Check CloudWatch logs for details."
        ok "All builds completed successfully."
        ;;
    *)
        die "Unknown target: $TARGET (use coding-agent|sandbox|all)"
        ;;
esac
