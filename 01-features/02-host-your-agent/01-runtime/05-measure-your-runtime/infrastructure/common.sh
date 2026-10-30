#!/usr/bin/env bash
# Shared configuration and helpers sourced by the infrastructure scripts.
#
# Prod only: every AgentCore call goes to the public regional endpoint.
#
# Override any value via environment variables, e.g.:
#   AWS_REGION=us-west-2 ECR_REPOSITORY=my-agent ./build-and-push.sh
set -euo pipefail

# ---- Configuration (override via environment) ----
# Must match benchmarks/.env's AWS_REGION -- the deploy scripts create the
# runtime here, and the benchmark invokes whatever AWS_REGION it reads out of
# .env. If they disagree, every invoke fails against a region that has no
# runtime. Keep this default and .env.example's in sync when changing either.
AWS_REGION="${AWS_REGION:-us-east-1}"

# ---- AgentCore session lifecycle ----
# How long an idle session survives, and its hard maximum lifetime (seconds).
# API range for both: 60..28800.
#
# 900/1200 (not the API's own defaults) because benchmarks/run_scenario.sh's
# scenario 1 container leg ramps at 400 units/min to a 5,000-unit target --
# ~750s just to reach the target, before any throttling stretches it further.
# At the previous default (300s), every session went idle immediately after
# its one invoke and was reaped by AgentCore mid-ramp: the ramp's own
# "active" counter never saw the drop (it only finds out at teardown, via
# ResourceNotFoundException -- see common.py's THROTTLE_MARKERS-adjacent
# teardown handling in load_ramp.py), so peak-fleet-size and session-hours
# were both overstated on any leg whose ramp outlasted the idle timeout. The
# zip leg's ramp (~200s) was never affected; the container leg's was, badly.
# Raising this is a real cost lever (see the top-level README's Cost
# section) -- override it down for a cheaper, shorter rehearsal.
AGENTCORE_IDLE_TIMEOUT="${AGENTCORE_IDLE_TIMEOUT:-900}"
AGENTCORE_MAX_LIFETIME="${AGENTCORE_MAX_LIFETIME:-1200}"
ECR_REPOSITORY="${ECR_REPOSITORY:-runtime-v2-test-agent}"
# A single shared image tag is used by BOTH runtimes so the comparison runs on
# the exact same image (same repo, same tag, same bits).
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE_PLATFORM="${IMAGE_PLATFORM:-linux/arm64}"

# Directory containing the base-agent Dockerfile (repo layout: ../base-agent).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_CONTEXT="${BUILD_CONTEXT:-${SCRIPT_DIR}/../base-agent}"
# Sample-root venv (../.venv from infrastructure/, i.e.
# 05-measure-your-runtime/.venv — the same one benchmarks/run_scenario.sh
# resolves to), for agentcore_boto3.py. Overridable in case yours lives
# somewhere else.
VENV_PYTHON="${VENV_PYTHON:-${SCRIPT_DIR}/../.venv/bin/python3}"

log()  { printf '\033[1;34m[infra]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[infra]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[infra]\033[0m %s\n' "$*" >&2; exit 1; }

require() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# Resolve the AWS account ID from the current credentials.
account_id() {
  aws sts get-caller-identity --query Account --output text
}

# The Python interpreter for agentcore_boto3.py: prefer this repo's own venv
# (a current, explicitly-managed boto3/botocore — and, via
# agentcore_boto3.py's own session setup, immune to a stale ~/.aws/models
# override shadowing a newly-released API field) over whatever `python3`
# happens to resolve first on PATH.
agentcore_python() {
  if [ -x "${VENV_PYTHON}" ]; then
    echo "${VENV_PYTHON}"
  else
    warn "repo venv python not found at ${VENV_PYTHON}, falling back to python3 on PATH" >&2
    echo "python3"
  fi
}

# Full ECR registry host, e.g. 123456789012.dkr.ecr.us-west-2.amazonaws.com
ecr_registry() {
  echo "$(account_id).dkr.ecr.${AWS_REGION}.amazonaws.com"
}

# The single shared image URI used by both runtimes,
# e.g. <registry>/base-agent:latest
image_uri() {
  echo "$(ecr_registry)/${ECR_REPOSITORY}:${IMAGE_TAG}"
}

# The --lifecycle-configuration JSON for the AgentCore deploy calls. Validated
# against the API model: both fields are integers in 60..28800 seconds.
agentcore_lifecycle_json() {
  local idle="${AGENTCORE_IDLE_TIMEOUT}" maxl="${AGENTCORE_MAX_LIFETIME}"
  for v in "${idle}" "${maxl}"; do
    case "${v}" in
      ''|*[!0-9]*) die "lifecycle values must be integers (got '${v}')" ;;
    esac
    if [ "${v}" -lt 60 ] || [ "${v}" -gt 28800 ]; then
      die "lifecycle value ${v}s out of the API range 60..28800"
    fi
  done
  # An idle timeout above the max lifetime is silently useless — the session is
  # reaped by maxLifetime before it can ever go idle that long.
  if [ "${idle}" -gt "${maxl}" ]; then
    die "AGENTCORE_IDLE_TIMEOUT (${idle}s) exceeds AGENTCORE_MAX_LIFETIME (${maxl}s)"
  fi
  echo "{\"idleRuntimeSessionTimeout\":${idle},\"maxLifetime\":${maxl}}"
}

# Create an IAM role, or update it in place if it already exists.
# On re-runs this refreshes both the trust policy and the inline permissions
# policy, so adding a missing permission just requires re-running the deploy.
#
# Args: <role-name> <trust-policy-json> <inline-policy-name> <inline-policy-json>
# Echoes the role ARN on stdout.
ensure_role() {
  local role_name="$1" trust_policy="$2" policy_name="$3" policy_doc="$4"
  local role_arn is_new=0

  if aws iam get-role --role-name "${role_name}" >/dev/null 2>&1; then
    log "Updating trust policy for existing role '${role_name}'..." >&2
    aws iam update-assume-role-policy \
      --role-name "${role_name}" \
      --policy-document "${trust_policy}" >/dev/null
  else
    log "Creating IAM role '${role_name}'..." >&2
    aws iam create-role \
      --role-name "${role_name}" \
      --assume-role-policy-document "${trust_policy}" >/dev/null
    is_new=1
  fi

  # put-role-policy is create-or-replace, so it also handles updates.
  log "Applying inline policy '${policy_name}'..." >&2
  aws iam put-role-policy \
    --role-name "${role_name}" \
    --policy-name "${policy_name}" \
    --policy-document "${policy_doc}" >/dev/null

  # The role's policy takes time to propagate before a service can validate
  # permissions against it — true for a brand-new role, but ALSO true for an
  # existing role whose inline policy just changed (put-role-policy is
  # create-or-replace, so this runs on every call). Seen in practice: deploying
  # right after recreating the ECR repo referenced in this policy got
  # "Access denied while validating ECR URI" from AgentCore, even though the
  # policy content itself was already correct — a propagation race, not a
  # permissions bug. Wait AFTER the policy is attached regardless of is_new.
  if [ "${is_new}" -eq 1 ]; then
    log "Waiting for role + policy propagation (new role)..." >&2
    sleep 15
  else
    log "Waiting for policy propagation..." >&2
    sleep 10
  fi

  role_arn="$(aws iam get-role --role-name "${role_name}" \
    --query 'Role.Arn' --output text)"
  echo "${role_arn}"
}
