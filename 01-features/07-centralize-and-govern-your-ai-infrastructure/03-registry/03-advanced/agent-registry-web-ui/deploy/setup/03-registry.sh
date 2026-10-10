#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Layer 03 - Registry data + persona users.
#   up   : run deploy/seed-registry.sh (create registry $REGISTRY_NAME, default
#          'AgentRegistryDemo', seed 9
#          records across all types x lifecycle, create the 3 persona users), then
#          sync the new REGISTRY_ID into frontend/.env.
#   down : delete all records, the registry, AND the seeded users - leaving the
#          Cognito User Pool itself intact (that belongs to layer 01).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
SEED="$DEPLOY_DIR/seed-registry.sh"
ACTION="${1:-up}"

registry_up() {
  step "Layer 03 UP - Seed registry + records + persona users"
  [ -f "$STATE_FILE" ] || die "missing $STATE_FILE - run ./02-config.sh up first"
  [ -f "$SEED" ] || die "missing $SEED"
  # Re-run safety: seeding always creates a NEW registry and overwrites REGISTRY_ID in
  # state, which would strand the previous one (teardown only knows the state id).
  PREV_REGISTRY_ID="$(grep -E '^REGISTRY_ID=' "$STATE_FILE" | tail -1 | cut -d= -f2 || true)"
  PREV_STATUS=""
  if [ -n "$PREV_REGISTRY_ID" ]; then
    PREV_STATUS="$(aws agent-registry-control get-registry --registry-id "$PREV_REGISTRY_ID" \
      --query status --output text 2>/dev/null || true)"
  fi
  case "$PREV_STATUS" in
    ""|None|*FAILED*|DELETING) SKIP_SEED=0 ;;
    *) SKIP_SEED=1 ;;
  esac
  if [ "$SKIP_SEED" = "1" ]; then
    ok "registry $PREV_REGISTRY_ID from state already exists ($PREV_STATUS) - not seeding again"
    info "To recreate it (e.g. to change REGISTRY_AUTH_MODE), run ./03-registry.sh down first."
  else
    info "Creates the registry '${REGISTRY_NAME:-AgentRegistryDemo}' (CUSTOM_JWT authorizer bound to this"
    info "sample's Cognito user pool, manual approval), 9 tagged records (DRAFT/PENDING/APPROVED),"
    info "and approver@/publisher@/consumer@example.com. Set PERSONA_PASSWORD to choose the"
    info "password, or REGISTRY_AUTH_MODE=AWS_IAM for a SigV4-authorized registry."
    info "NOTE: a registry's authorizer type is IMMUTABLE - switching means a new registry."
    step "Running the seed script"
    if ! run bash "$SEED"; then
      err "seed script FAILED - not syncing config. Fix the error above and re-run."
      err "(a registry may have been created before the failure - check with:"
      err " aws agent-registry-control list-registries --region $AWS_REGION)"
      exit 1
    fi
  fi
  step "Syncing REGISTRY_ID + REGISTRY_AUTH_MODE into frontend/.env"
  REGISTRY_ID="$(grep -E '^REGISTRY_ID=' "$STATE_FILE" | tail -1 | cut -d= -f2 || true)"
  [ -n "$REGISTRY_ID" ] || die "REGISTRY_ID not found in $STATE_FILE - did seeding fail?"
  REGISTRY_AUTH_MODE="$(grep -E '^REGISTRY_AUTH_MODE=' "$STATE_FILE" | tail -1 | cut -d= -f2 || true)"
  REGISTRY_AUTH_MODE="${REGISTRY_AUTH_MODE:-CUSTOM_JWT}"
  info "REGISTRY_ID=$REGISTRY_ID"
  info "REGISTRY_AUTH_MODE=$REGISTRY_AUTH_MODE"
  # The app must agree with the registry: CUSTOM_JWT routes discovery through the
  # registry's MCP endpoint with a bearer token; AWS_IAM uses the SigV4 SDK client.
  set_env_var() {  # set_env_var KEY VALUE  (update in place, else append)
    if grep -qE "^$1=" "$FRONTEND_ENV" 2>/dev/null; then
      run sed -i.bak "s#^$1=.*#$1=$2#" "$FRONTEND_ENV"; rm -f "$FRONTEND_ENV.bak"
    else
      echo "$1=$2" >> "$FRONTEND_ENV"
    fi
  }
  set_env_var VITE_REGISTRY_ID "$REGISTRY_ID"
  set_env_var VITE_REGISTRY_AUTH_MODE "$REGISTRY_AUTH_MODE"
  ok "frontend/.env now points at registry $REGISTRY_ID ($REGISTRY_AUTH_MODE)"
  step "Layer 03 UP complete"
  info "Next: ./04-run-local.sh   (or ./05-site.sh up to deploy publicly)"
}

registry_down() {
  step "Layer 03 DOWN - Delete registry + records + the seeded persona users"
  load_state_ids
  REGISTRY_ID="${REGISTRY_ID:-}"; USER_POOL_ID="${USER_POOL_ID:-}"

  if [ -z "$REGISTRY_ID" ]; then
    ok "no REGISTRY_ID in state; no registry to delete"
  elif ! aws agent-registry-control get-registry --registry-id "$REGISTRY_ID" >/dev/null 2>&1; then
    ok "registry $REGISTRY_ID already gone"
  else
    info "Deleting all records in registry $REGISTRY_ID"
    for rid in $(aws agent-registry-control list-registry-records --registry-id "$REGISTRY_ID" \
                   --query 'registryRecords[].recordId' --output text 2>/dev/null); do
      run aws agent-registry-control delete-registry-record --registry-id "$REGISTRY_ID" --record-id "$rid" >/dev/null 2>&1 \
        && ok "deleted record $rid"
    done
    sleep 6
    info "Deleting the registry"
    run aws agent-registry-control delete-registry --registry-id "$REGISTRY_ID" >/dev/null 2>&1 \
      && ok "registry delete issued (async)" || warn "registry delete failed"
  fi

  step "Deleting the seeded persona users (pool itself left for layer 01)"
  if [ -z "$USER_POOL_ID" ]; then
    ok "no USER_POOL_ID in state; no users to delete"
  elif ! aws cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" >/dev/null 2>&1; then
    ok "user pool $USER_POOL_ID already gone (users went with it)"
  else
    # Three current personas, plus two legacy emails from the earlier 4-persona
    # layout so an older deployment is also cleaned up (a missing user is a no-op).
    for email in approver@example.com publisher@example.com consumer@example.com \
                 curator@example.com admin@example.com; do
      if aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$email" >/dev/null 2>&1; then
        run aws cognito-idp admin-delete-user --user-pool-id "$USER_POOL_ID" --username "$email" 2>/dev/null \
          && ok "deleted $email" || warn "failed to delete $email"
      else ok "$email already gone"; fi
    done
  fi
  step "Layer 03 DOWN complete"
}

case "$ACTION" in
  up)   registry_up ;;
  down) registry_down ;;
  *)    die "usage: $0 up|down" ;;
esac
