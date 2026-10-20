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

load_saved_persona_password() {
  local pw_file="$STATE_DIR/persona-password.txt" pw_problem
  [ -s "$pw_file" ] || die "saved persona password is missing; restore $pw_file or recreate layer 03"
  PERSONA_PASSWORD="$(cat "$pw_file")"
  pw_problem="$(LC_ALL=C bash -c '
    p="$1"; prob=""
    [ ${#p} -ge 12 ] || prob="must be at least 12 characters"
    case "$p" in *[A-Z]*) ;; *) prob="must have an uppercase character";; esac
    case "$p" in *[a-z]*) ;; *) prob="must have a lowercase character";; esac
    case "$p" in *[0-9]*) ;; *) prob="must have a digit";; esac
    case "$p" in *[!A-Za-z0-9]*) ;; *) prob="must have a symbol";; esac
    printf "%s" "$prob"
  ' _ "$PERSONA_PASSWORD")"
  [ -z "$pw_problem" ] || die "saved persona password $pw_problem; restore a valid generated password or recreate layer 03"
}

load_persona_status() {
  local email="$1" err_file
  err_file="$(mktemp)"
  if PERSONA_STATUS="$(aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" \
      --username "$email" --query UserStatus --output text 2>"$err_file")"; then
    rm -f "$err_file"
  elif grep -q 'UserNotFoundException' "$err_file"; then
    PERSONA_STATUS=""
    rm -f "$err_file"
  else
    cat "$err_file" >&2
    rm -f "$err_file"
    die "could not inspect generated persona $email"
  fi
}

reconcile_persona_users() {
  local spec email group user_status groups need_password=0
  local specs=(
    "approver@example.com:AgentRegistryApprover"
    "publisher@example.com:AgentRegistryPublisher"
    "consumer@example.com:AgentRegistryConsumer"
  )

  # Resolve and validate the saved credential before the first write whenever a
  # missing or incomplete user needs it. Confirmed users keep their current password.
  for spec in "${specs[@]}"; do
    email="${spec%%:*}"
    load_persona_status "$email"
    user_status="$PERSONA_STATUS"
    if [ -z "$user_status" ] || [ "$user_status" != "CONFIRMED" ]; then
      need_password=1
    fi
  done
  [ "$need_password" = "0" ] || load_saved_persona_password

  for spec in "${specs[@]}"; do
    email="${spec%%:*}"; group="${spec#*:}"
    load_persona_status "$email"
    user_status="$PERSONA_STATUS"
    if [ -z "$user_status" ]; then
      info "Creating missing generated persona $email"
      aws cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" \
        --username "$email" --message-action SUPPRESS \
        --user-attributes Name=email,Value="$email" Name=email_verified,Value=true >/dev/null
      user_status="FORCE_CHANGE_PASSWORD"
    fi
    if [ "$user_status" != "CONFIRMED" ]; then
      info "Completing generated persona $email"
      # Do not use run here: its command echo would expose the saved password.
      aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" \
        --username "$email" --password "$PERSONA_PASSWORD" --permanent >/dev/null \
        || die "could not apply the saved persona password to $email"
    fi
    groups="$(aws cognito-idp admin-list-groups-for-user --user-pool-id "$USER_POOL_ID" \
      --username "$email" --query 'Groups[].GroupName' --output text)"
    if ! grep -qw "$group" <<<"$groups"; then
      info "Adding $email to $group"
      run aws cognito-idp admin-add-user-to-group --user-pool-id "$USER_POOL_ID" \
        --username "$email" --group-name "$group" >/dev/null
    fi
    ok "$email is CONFIRMED and belongs to $group"
  done
  unset PERSONA_PASSWORD
}

load_registry_status() {
  local err_file
  err_file="$(mktemp)"
  if PREV_STATUS="$(aws agent-registry-control get-registry --registry-id "$PREV_REGISTRY_ID" \
      --query status --output text 2>"$err_file")"; then
    rm -f "$err_file"
  elif grep -q 'ResourceNotFoundException' "$err_file"; then
    PREV_STATUS=""
    rm -f "$err_file"
  else
    cat "$err_file" >&2
    rm -f "$err_file"
    die "could not inspect registry $PREV_REGISTRY_ID; refusing to seed a replacement"
  fi
}

verify_existing_registry() {
  local registry_json registry_status actual_mode expected_discovery actual_discovery client_allowed
  registry_json="$(aws agent-registry-control get-registry --registry-id "$PREV_REGISTRY_ID" --output json)" \
    || die "could not inspect registry $PREV_REGISTRY_ID"
  registry_status="$(jq -r '.status // empty' <<<"$registry_json")"
  [ "$registry_status" = "READY" ] || die "registry $PREV_REGISTRY_ID is $registry_status; wait for READY and rerun layer 03"

  actual_mode="$(jq -r '.discoveryConfiguration.authorizerType // empty' <<<"$registry_json")"
  [ "$actual_mode" = "$REGISTRY_AUTH_MODE" ] || die "registry authorization mode does not match current state. Run ./03-registry.sh down, then ./03-registry.sh up after reviewing the destructive impact."
  if [ "$actual_mode" = "CUSTOM_JWT" ]; then
    expected_discovery="https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"
    actual_discovery="$(jq -r '.discoveryConfiguration.authorizerConfiguration.customJWTAuthorizer.discoveryUrl // empty' <<<"$registry_json")"
    client_allowed="$(jq -r --arg client "$USER_POOL_CLIENT_ID" '(.discoveryConfiguration.authorizerConfiguration.customJWTAuthorizer.allowedClients // []) | index($client) != null' <<<"$registry_json")"
    if [ "$actual_discovery" != "$expected_discovery" ] || [ "$client_allowed" != "true" ]; then
      die "registry JWT authorizer belongs to a different Cognito foundation. Existing state was preserved. Recovery requires ./03-registry.sh down followed by ./03-registry.sh up; review that destructive registry replacement before running it."
    fi
  fi
  ok "existing registry authorizer matches the current Cognito foundation"
}

registry_up() {
  step "Layer 03 UP - Seed registry + records + persona users"
  [ -f "$STATE_FILE" ] || die "missing $STATE_FILE - run ./02-config.sh up first"
  [ -f "$SEED" ] || die "missing $SEED"
  need jq
  load_state_ids
  : "${USER_POOL_ID:?USER_POOL_ID missing from state - run ./02-config.sh up first}"
  : "${USER_POOL_CLIENT_ID:?USER_POOL_CLIENT_ID missing from state - run ./02-config.sh up first}"
  REGISTRY_AUTH_MODE="${REGISTRY_AUTH_MODE:-CUSTOM_JWT}"
  # Re-run safety: seeding always creates a NEW registry and overwrites REGISTRY_ID in
  # state, which would strand the previous one (teardown only knows the state id).
  PREV_REGISTRY_ID="$(grep -E '^REGISTRY_ID=' "$STATE_FILE" | tail -1 | cut -d= -f2 || true)"
  PREV_STATUS=""
  if [ -n "$PREV_REGISTRY_ID" ]; then
    load_registry_status
  fi
  case "$PREV_STATUS" in
    ""|None|*FAILED*|DELETING) SKIP_SEED=0 ;;
    *) SKIP_SEED=1 ;;
  esac
  if [ "$SKIP_SEED" = "1" ]; then
    verify_existing_registry
    reconcile_persona_users
    ok "registry $PREV_REGISTRY_ID from state is ready and generated personas are reconciled"
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
