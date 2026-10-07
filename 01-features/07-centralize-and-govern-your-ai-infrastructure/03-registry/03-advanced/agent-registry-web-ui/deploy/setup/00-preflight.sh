#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Step 0 - Preflight. Verifies your tooling + AWS identity and reports the
# current state of the resources this setup will create. Read-only: it changes
# nothing. Run it first to confirm you are pointed at the right account/region.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

step "Step 0/5 - Preflight checks (read-only, nothing is created)"

info "Checking required CLI tools are installed"
need aws; ok "aws CLI found ($(aws --version 2>&1 | head -1))"
need node; ok "node found ($(node -v))"
need npm;  ok "npm found ($(npm -v))"
if command -v jq >/dev/null 2>&1; then ok "jq found"; else warn "jq not found (required by 06-sample-mcp-gateway.sh)"; fi
if command -v zip >/dev/null 2>&1; then ok "zip found"; else warn "zip not found (required by 06-sample-mcp-gateway.sh)"; fi

info "Confirming which AWS account/identity these scripts will act on"
run_capture ACCOUNT aws sts get-caller-identity --query Account --output text
run_capture ARN     aws sts get-caller-identity --query Arn     --output text
ok "Account: $ACCOUNT"
ok "Identity: $ARN"
ok "Region: $AWS_REGION   Stack: $STACK_NAME   AppName: $APP_NAME"

step "Current resource state (so you know if this is a clean slate)"

info "CloudFormation stack '$STACK_NAME'"
if run aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
      --query 'Stacks[0].StackStatus' --output text 2>/dev/null; then
  warn "Stack '$STACK_NAME' already exists (Step 1 will UPDATE it, not create)."
else
  ok "No existing stack named '$STACK_NAME' (clean slate)."
fi

info "Existing Agent Registries in this account/region"
run aws agent-registry-control list-registries --region "$AWS_REGION" \
  --query 'registries[].{id:registryId,name:name,status:status}' --output table 2>/dev/null \
  || warn "Could not list registries (service/permissions?)."

info "Local config files"
[ -f "$FRONTEND_ENV" ] && warn "frontend/.env exists (Step 2 will overwrite it)" || ok "frontend/.env not present yet"
[ -f "$STATE_FILE" ]   && warn "deploy/state/outputs.env exists (Step 2 will overwrite it)" || ok "deploy/state/outputs.env not present yet"

step "Preflight complete"
info "Next: run ./01-foundation.sh up  (you run this - it deploys IAM + Cognito via CloudFormation)"
