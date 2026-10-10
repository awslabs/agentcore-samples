#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Orchestrator - run every layer's up (in order) or down (in reverse).
#   ./99-all.sh up     01 foundation -> 02 config -> 03 registry -> 06 sample gateway -> 05 site
#   ./99-all.sh down   05 site -> 06 sample gateway -> 03 registry -> 02 config -> 01 foundation
#
# Layer 06 runs BEFORE 05 on the way up so the site is built with the sample
# gateway's credential-provider ARN already in frontend/.env.
#
# NOTE: layer 01 up creates named IAM roles through CloudFormation and therefore
# needs CAPABILITY_NAMED_IAM permissions. `down` deletes
# the CFN stack by default; DIRECT=1 makes layer 01 down delete Cognito/IAM
# directly instead. FORCE=1 skips the down confirmation.
# SKIP_SAMPLE=1 leaves layer 06 (the sample gateway) out of both directions.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACTION="${1:-}"

case "$ACTION" in
  up)
    step "FULL SETUP - running every layer up, in order"
    # Abort the moment a layer fails - deploying a later layer on top of a failed
    # one (e.g. publishing the site after the seed failed) hides the real error.
    LAYERS="01-foundation 02-config 03-registry"
    [ "${SKIP_SAMPLE:-0}" = "1" ] || LAYERS="$LAYERS 06-sample-mcp-gateway"
    LAYERS="$LAYERS 05-site"
    for layer in $LAYERS; do
      if ! bash "$HERE/$layer.sh" up; then
        err "layer $layer FAILED - stopping. Fix the error above, then re-run (layers are idempotent)."
        exit 1
      fi
    done
    step "FULL SETUP complete"
    ;;
  down)
    step "FULL TEARDOWN - running every layer down, in reverse. Permanently deletes ALL demo resources."
    run_capture ACCOUNT aws sts get-caller-identity --query Account --output text 2>/dev/null || true
    info "Account: ${ACCOUNT:-unknown}   Region: $AWS_REGION"
    if [ "${FORCE:-0}" != "1" ]; then
      printf '%s' "Type 'delete' to confirm: "; read -r confirm || true
      [ "$confirm" = "delete" ] || { err "aborted"; exit 1; }
    fi
    # Teardown is best-effort: keep going so one stuck resource cannot strand the
    # rest, but remember the failure and exit non-zero at the end.
    rc=0
    bash "$HERE/05-site.sh" down || { err "05-site down reported a problem"; rc=1; }
    if [ "${SKIP_SAMPLE:-0}" != "1" ]; then
      # Before 03: the sample record lives in the registry that layer deletes.
      bash "$HERE/06-sample-mcp-gateway.sh" down || { err "06-sample-mcp-gateway down reported a problem"; rc=1; }
    fi
    bash "$HERE/03-registry.sh" down || { err "03-registry down reported a problem"; rc=1; }
    DIRECT="${DIRECT:-0}" bash "$HERE/01-foundation.sh" down || { err "01-foundation down reported a problem"; rc=1; }
    bash "$HERE/02-config.sh" down || { err "02-config down reported a problem"; rc=1; }
    if [ "$rc" -eq 0 ]; then
      step "FULL TEARDOWN complete"
    else
      step "FULL TEARDOWN finished WITH PROBLEMS - re-run to retry the failed layers"
    fi
    exit "$rc"
    ;;
  *)
    die "usage: $0 up|down   (down: FORCE=1 skips prompt, DIRECT=1 deletes Cognito/IAM without the stack)"
    ;;
esac
