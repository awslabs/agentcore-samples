#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Shared helpers for the Agent Registry UI setup scripts.
# Source this at the top of each step script:  source "$(dirname "$0")/_lib.sh"
#
# It provides verbose, colourised logging so the user always sees WHAT the
# script is doing and the EXACT command it runs against their AWS account.

# --- config (override via env) ---------------------------------------------
export AWS_REGION="${AWS_REGION:-us-east-1}"
export APP_NAME="${APP_NAME:-agentregistry-ui}"
# Defaults to APP_NAME so one variable isolates a second copy in the same account.
export STACK_NAME="${STACK_NAME:-$APP_NAME}"

# Resolve repo paths relative to this file, regardless of caller CWD.
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$_LIB_DIR/.." && pwd)"; export DEPLOY_DIR   # .../deploy
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"; export REPO_ROOT    # sample root
export FRONTEND_DIR="$REPO_ROOT/frontend"
export STATE_DIR="$DEPLOY_DIR/state"
export STATE_FILE="$STATE_DIR/outputs.env"
export FRONTEND_ENV="$FRONTEND_DIR/.env"

# --- colours (auto-off when not a TTY) -------------------------------------
if [ -t 1 ]; then
  _C_RESET=$'\033[0m'; _C_BOLD=$'\033[1m'; _C_DIM=$'\033[2m'
  _C_BLUE=$'\033[34m'; _C_GREEN=$'\033[32m'; _C_YELLOW=$'\033[33m'; _C_RED=$'\033[31m'; _C_CYAN=$'\033[36m'
else
  _C_RESET=""; _C_BOLD=""; _C_DIM=""; _C_BLUE=""; _C_GREEN=""; _C_YELLOW=""; _C_RED=""; _C_CYAN=""
fi

# --- logging ----------------------------------------------------------------
# step: a major phase header
step()  { printf '\n%s%s==> %s%s\n' "$_C_BOLD" "$_C_BLUE" "$*" "$_C_RESET"; }
# info: an explanation of WHY / WHAT is about to happen
info()  { printf '%s  - %s%s\n' "$_C_CYAN" "$*" "$_C_RESET"; }
# ok / warn / err: outcomes
ok()    { printf '%s  OK %s%s\n' "$_C_GREEN" "$*" "$_C_RESET"; }
warn()  { printf '%s  !! %s%s\n' "$_C_YELLOW" "$*" "$_C_RESET"; }
err()   { printf '%s  XX %s%s\n' "$_C_RED" "$*" "$_C_RESET" >&2; }

# run: echo the EXACT command (so the user sees what hits their account), then run it.
run() {
  printf '%s    $ %s%s\n' "$_C_DIM" "$*" "$_C_RESET"
  "$@"
}

# run_capture: same, but capture stdout into a variable named by $1.
#   usage: run_capture VAR aws sts get-caller-identity --query Account --output text
run_capture() {
  local __var="$1"; shift
  printf '%s    $ %s%s\n' "$_C_DIM" "$*" "$_C_RESET"
  # A failed command leaves the variable empty; callers check for that and decide
  # (so this stays safe under `set -e`).
  local __out; __out="$("$@")" || true
  printf -v "$__var" '%s' "$__out"
}

# die: fatal error + exit
die() { err "$*"; exit 1; }

# need: assert a binary is on PATH
need() { command -v "$1" >/dev/null 2>&1 || die "required tool not found on PATH: $1"; }

# load_state_ids: source deploy/state/outputs.env after clearing the id variables it
# holds, so a layer acts ONLY on ids recorded by this deployment, never on same-named
# variables inherited from the caller's shell (e.g. an exported REGISTRY_ID for some
# other registry would otherwise be deleted by `03-registry.sh down`).
# SITE_BUCKET is kept: it is a documented override for 05-site.sh.
load_state_ids() {
  unset USER_POOL_ID USER_POOL_CLIENT_ID IDENTITY_POOL_ID PROVIDER REGION \
    REGISTRY_ARN REGISTRY_ID REGISTRY_AUTH_MODE SITE_OAC_ID SITE_DIST_ID SITE_DOMAIN \
    SAMPLE_GW_ID SAMPLE_GW_URL SAMPLE_GW_TARGET_ID SAMPLE_OAUTH_PROVIDER_ARN \
    SAMPLE_M2M_CLIENT_ID SAMPLE_COGNITO_DOMAIN SAMPLE_RECORD_ID
  # shellcheck disable=SC1090
  if [ -f "$STATE_FILE" ]; then source "$STATE_FILE"; fi
}

# aws_partition: the caller's ARN partition (aws, aws-cn, aws-us-gov), so ARNs
# built here match the CloudFormation template's ${AWS::Partition}.
aws_partition() {
  local p
  p="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null | cut -d: -f2 || true)"
  printf '%s' "${p:-aws}"
}
