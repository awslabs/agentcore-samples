#!/usr/bin/env bash
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# cleanup.sh — Tear down everything demo.sh created, so no billable resources leak.
#
# Removal order matters: remove the harness from the project spec and re-deploy
# (the deployer deletes resources that are in deployed-state but no longer in the
# spec), then delete the CDK stack that holds the IAM execution role and memory.
#
# Usage:
#   ./cleanup.sh
#   AWS_REGION=us-west-2 ./cleanup.sh

set -euo pipefail

# Read what demo.sh created (zero-args teardown). Env vars still override if set.
STATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.demo-state"
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
fi

REGION="${AWS_REGION:-${REGION:-us-east-1}}"
PROJECT_NAME="${PROJECT_NAME:-agentcoreqa}"
HARNESS_NAME="${HARNESS_NAME:-acqa_assistant}"
WORKDIR="${WORKDIR:-$(pwd)/.demo-workspace-${PROJECT_NAME}}"
STACK_NAME="AgentCore-${PROJECT_NAME}-default"

if [[ -t 1 ]]; then BOLD=$'\033[1m'; YELLOW=$'\033[33m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
else BOLD=""; YELLOW=""; GREEN=""; RESET=""; fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '')"
_OS_USER="$(id -un 2>/dev/null || echo "${USER:-}")"
mask() {
  local -a args=()
  [[ -n "$HOME" ]]     && args+=(-e "s|${HOME}|/Users/<USER>|g")
  [[ -n "$_OS_USER" ]] && args+=(-e "s|/Users/${_OS_USER}|/Users/<USER>|g" \
                                 -e "s|/home/${_OS_USER}|/home/<USER>|g" \
                                 -e "s/${_OS_USER}/<USER>/g")
  [[ -n "$ACCOUNT_ID" ]] && args+=(-e "s/${ACCOUNT_ID}/<ACCOUNT>/g")
  args+=(-e 's/[0-9]\{12\}/<ACCOUNT>/g')
  sed "${args[@]}"
}
run() { echo "${GREEN}\$ $*${RESET}" | mask; "$@" 2>&1 | mask; return "${PIPESTATUS[0]}"; }

echo "${BOLD}Tearing down the observability demo (region ${REGION})${RESET}"

if [[ -d "$WORKDIR/$PROJECT_NAME" ]]; then
  cd "$WORKDIR/$PROJECT_NAME"
  # Removing the harness from the spec also drops its auto-created memory, so a
  # single remove is enough. Once the project spec is empty, `deploy --yes` tears
  # down all deployed resources and the CloudFormation stack.
  echo "Removing the harness from the project spec..."
  AWS_REGION="$REGION" run agentcore remove harness --name "$HARNESS_NAME" -y || true
  echo "Tearing down deployed resources (agentcore deploy --yes on an empty spec)..."
  AWS_REGION="$REGION" run agentcore deploy --target default --yes || \
    echo "${YELLOW}CLI teardown reported an issue; the stack delete below is the backstop.${RESET}"
fi

echo "Ensuring the CDK stack is deleted (backstop / direct path if no local workspace)..."
run aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION" || true
echo "Waiting for stack deletion to complete..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null \
  && echo "${GREEN}✓ Stack deleted.${RESET}" \
  || echo "${YELLOW}Stack delete still in progress or already gone — check the CloudFormation console.${RESET}"

echo "Removing the local workspace and run state..."
rm -rf "$WORKDIR"
rm -f "$STATE_FILE"
echo "${BOLD}Cleanup complete.${RESET}"
echo "${GREEN}The next ./demo.sh run auto-generates a fresh unique name, so you can record again immediately.${RESET}"
