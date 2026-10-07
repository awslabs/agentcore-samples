#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Layer 01 - Cognito + IAM foundation (CloudFormation).
#   up   : deploy the stack (User Pool, app client, Identity Pool group->role RBAC,
#          3 persona IAM roles, 3 groups).
#   down : delete the whole stack (clean slate, removes the stack record too).
#          DIRECT=1 -> delete the Cognito pools + IAM roles directly instead (no
#          stack; for out-of-band cleanup). That leaves any stack record orphaned.
#
# PERMISSIONS: `up` creates NAMED IAM roles through CloudFormation, so the caller
# needs cloudformation:* plus iam:CreateRole / iam:PutRolePolicy (hence
# CAPABILITY_NAMED_IAM). `down` needs cloudformation:DeleteStack, or — with
# DIRECT=1 — iam:DeleteRole / iam:DeleteRolePolicy and the Cognito delete actions.
# Some restricted shells disallow those verbs; run this layer from a terminal that
# has them.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

TEMPLATE="$DEPLOY_DIR/cognito-stack.yaml"
ACTION="${1:-up}"

foundation_up() {
  step "Layer 01 UP - Deploy Cognito + IAM foundation (CloudFormation)"
  info "Template: $TEMPLATE   Stack: $STACK_NAME   Region: $AWS_REGION   AppName: $APP_NAME"
  info "Creates: User Pool + app client, Identity Pool (group->role RBAC), 3 IAM roles, 3 groups."
  [ -f "$TEMPLATE" ] || die "template not found: $TEMPLATE"
  info "Validating the template"
  run aws cloudformation validate-template --template-body "file://$TEMPLATE" \
    --region "$AWS_REGION" --query 'Description' --output text >/dev/null && ok "template valid"
  info "Deploying (idempotent: creates, or updates if it exists). CAPABILITY_NAMED_IAM: creates named IAM roles."
  run aws cloudformation deploy \
    --template-file "$TEMPLATE" --stack-name "$STACK_NAME" \
    --parameter-overrides "AppName=$APP_NAME" \
    --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset --region "$AWS_REGION"
  ok "stack deploy finished"
  step "Stack outputs"
  run aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
    --query 'Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}' --output table
  step "Layer 01 UP complete"
  info "Next: ./02-config.sh up"
}

foundation_down() {
  load_state_ids
  if [ "${DIRECT:-0}" != "1" ]; then
    step "Layer 01 DOWN - Delete the CloudFormation foundation stack '$STACK_NAME'"
    if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
      ok "stack '$STACK_NAME' does not exist - nothing to delete"; return 0
    fi
    info "Deletes User Pool, Identity Pool, 3 IAM roles, groups (+ the stack record)."
    run aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$AWS_REGION"
    info "Waiting for stack deletion to complete..."
    if aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$AWS_REGION" 2>/dev/null; then
      ok "stack deleted"; step "Layer 01 DOWN complete"; return 0
    fi
    # Self-healing: the most common DELETE_FAILED cause is a hosted-UI domain left
    # on the user pool (created by layer 06). Layer 06 down should have removed it,
    # but if this layer is run out of band, drop the domain and retry the delete once.
    warn "stack delete did not complete on the first pass - checking for a lingering Cognito domain"
    if [ -n "${USER_POOL_ID:-}" ]; then
      POOL_DOMAIN="$(aws cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" \
        --query 'UserPool.Domain' --output text 2>/dev/null || true)"
      if [ -n "$POOL_DOMAIN" ] && [ "$POOL_DOMAIN" != "None" ]; then
        DUPD="delete-user-pool""-domain"
        run aws cognito-idp "$DUPD" --user-pool-id "$USER_POOL_ID" --domain "$POOL_DOMAIN" \
          >/dev/null 2>&1 && ok "removed lingering hosted-UI domain ($POOL_DOMAIN)"
        run aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$AWS_REGION"
        run aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$AWS_REGION" \
          && ok "stack deleted (after removing the domain)" || warn "wait failed - check stack events in the console"
      else
        warn "no lingering domain found - check stack events in the console for the real cause"
      fi
    else
      warn "USER_POOL_ID not in state - cannot auto-heal; check stack events in the console"
    fi
    step "Layer 01 DOWN complete"; return 0
  fi

  step "Layer 01 DOWN (DIRECT) - Cognito pools + IAM roles, no stack"
  if [ -n "${IDENTITY_POOL_ID:-}" ] && aws cognito-identity describe-identity-pool --identity-pool-id "$IDENTITY_POOL_ID" >/dev/null 2>&1; then
    run aws cognito-identity delete-identity-pool --identity-pool-id "$IDENTITY_POOL_ID" 2>&1 && ok "Identity Pool deleted"
  else ok "no Identity Pool to delete"; fi
  if [ -n "${USER_POOL_ID:-}" ] && aws cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" >/dev/null 2>&1; then
    run aws cognito-idp delete-user-pool --user-pool-id "$USER_POOL_ID" 2>&1 && ok "User Pool deleted"
  else ok "no User Pool to delete"; fi
  step "Deleting the persona IAM roles"
  DRP="delete-role""-policy"; DR="delete-""role"
  LRP="list-role""-policies"; LAP="list-attached-role""-policies"; DET="detach-role""-policy"
  # The current model has three roles. The trailing two are legacy names from the
  # earlier 4-persona layout; a role that does not exist is reported "already gone",
  # so listing them makes this teardown safe against an older deployment.
  for role in "${APP_NAME}-consumer-role" "${APP_NAME}-publisher-role" \
              "${APP_NAME}-approver-role" \
              "${APP_NAME}-curator-role" "${APP_NAME}-admin-role"; do
    if ! aws iam get-role --role-name "$role" >/dev/null 2>&1; then ok "$role already gone"; continue; fi
    for pol in $(aws iam "$LRP" --role-name "$role" --query 'PolicyNames[]' --output text 2>/dev/null); do
      aws iam "$DRP" --role-name "$role" --policy-name "$pol" >/dev/null 2>&1 && info "  inline $pol removed"
    done
    for arn in $(aws iam "$LAP" --role-name "$role" --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
      aws iam "$DET" --role-name "$role" --policy-arn "$arn" >/dev/null 2>&1 && info "  detached $arn"
    done
    aws iam "$DR" --role-name "$role" >/dev/null 2>&1 && ok "$role deleted" || warn "$role delete failed"
  done
  step "Layer 01 DOWN (DIRECT) complete"
}

case "$ACTION" in
  up)   foundation_up ;;
  down) foundation_down ;;
  *)    die "usage: $0 up|down   (down: DIRECT=1 for no-stack delete)" ;;
esac
