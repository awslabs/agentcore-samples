#!/usr/bin/env bash
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# demo.sh — Deploy an observable AgentCore Harness from scratch and prove that
# sessions, traces, and spans land in CloudWatch GenAI Observability.
#
# This script is intentionally verbose: it echoes every command before running
# it, so it reads well in a terminal recording. Your AWS account ID is detected
# at runtime (never hard-coded) and masked as "ACCOUNT" in all printed output,
# so the recording is safe to share.
#
# What it does:
#   1. Pre-flight checks (agentcore CLI, aws CLI, credentials, Transaction Search)
#   2. Scaffold an empty project and add a harness (Claude Sonnet 4.6, memory on)
#   3. Deploy (CDK creates the IAM execution role; the harness is created)
#   4. Invoke the harness across one session to generate telemetry
#   5. Query aws/spans to prove OpenTelemetry spans were emitted
#   6. Print the GenAI Observability console link
#
# Tear everything down afterwards with: ./cleanup.sh
#
# Usage:
#   ./demo.sh                 # uses AWS_REGION or defaults to us-east-1
#   AWS_REGION=us-west-2 ./demo.sh
#   ./demo.sh --self-test     # offline checks only (no AWS calls), for CI
#
# Prerequisites:
#   - AgentCore CLI:  npm install -g @aws/agentcore@0.30.0
#   - AWS CLI v2 + credentials for a harness preview region
#     (us-east-1, us-west-2, ap-southeast-2, eu-central-1)
#   - CloudWatch Transaction Search enabled once per account (the script checks
#     and prints the enable commands if it is not).

set -euo pipefail

# ── Configuration (zero manual steps) ─────────────────────────────────────────
# Every run gets a short unique suffix, so back-to-back takes never collide.
# Harness names are soft-deleted and briefly reserved after cleanup, so reusing a
# name would 409 — the auto suffix avoids that entirely. Override any value by
# exporting it before running; otherwise safe, unique defaults are generated.
REGION="${AWS_REGION:-us-east-1}"
RUN_ID="${RUN_ID:-$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]' | cut -c1-6)}"
PROJECT_NAME="${PROJECT_NAME:-acdemo${RUN_ID}}"     # <=23 chars, alphanumeric, starts with a letter
HARNESS_NAME="${HARNESS_NAME:-acdemo_${RUN_ID}}"    # <=48 chars, starts with a letter
MODEL_ID="${MODEL_ID:-global.anthropic.claude-sonnet-4-6}"
WORKDIR="${WORKDIR:-$(pwd)/.demo-workspace-${PROJECT_NAME}}"
SESSION_ID="acqa-demo-$(uuidgen | tr '[:upper:]' '[:lower:]')"   # >= 33 chars
# State file so cleanup.sh tears down EXACTLY what this run created — no args needed.
STATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.demo-state"

# ── Colors (disabled when not a TTY) ───────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; CYAN=""; GREEN=""; YELLOW=""; DIM=""; RESET=""
fi

# ACCOUNT_ID is filled in by preflight(); used only to build aws-targets.json and
# to drive masking. It is NEVER written to a committed file.
ACCOUNT_ID=""

# ── mask: scrub sensitive identifiers from displayed text ──────────────────────
# Redacts, for a recording-safe screen:
#   - the AWS account id (explicit, plus any stray 12-digit run)  -> ACCOUNT
#   - your OS home dir and username in printed paths (e.g. CLI "Log:" lines)
#     /Users/<you>/... or /home/<you>/...  -> /Users/USER/...
# It scrubs only what reaches the screen; the real values are still used to run.
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

# ── say / step: narration helpers ──────────────────────────────────────────────
say()  { echo "${DIM}$*${RESET}" | mask; }
step() { echo; echo "${BOLD}${CYAN}=== $* ===${RESET}"; }

# ── run: print the command (masked), then execute it (output masked) ───────────
# This is what makes the recording readable — viewers see the exact command.
run() {
  echo "${GREEN}\$ $*${RESET}" | mask
  # Execute the real (unmasked) command; mask only what reaches the screen.
  "$@" 2>&1 | mask
  return "${PIPESTATUS[0]}"
}

# ── preflight: tools, creds, region, Transaction Search ────────────────────────
preflight() {
  step "Step 0: Pre-flight checks"

  command -v agentcore >/dev/null 2>&1 || {
    echo "${YELLOW}agentcore CLI not found. Install: npm install -g @aws/agentcore@0.30.0${RESET}"; exit 1; }
  node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
    || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
  agentcore --version | grep -q '^0\.' \
    || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
  command -v aws >/dev/null 2>&1 || { echo "${YELLOW}aws CLI not found.${RESET}"; exit 1; }

  say "AgentCore CLI version:"; run agentcore --version

  # Resolve account (for deploy + masking). Masked on screen.
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  echo "${GREEN}\$ aws sts get-caller-identity --query Account --output text${RESET}"
  echo "ACCOUNT"   # we deliberately print the masked form
  say "Region: ${REGION}"

  # Transaction Search is the one account-level prerequisite for trace visibility.
  local dest
  dest="$(aws xray get-trace-segment-destination --region "$REGION" \
            --query Destination --output text 2>/dev/null || echo UNKNOWN)"
  if [[ "$dest" == "CloudWatchLogs" ]]; then
    echo "${GREEN}Transaction Search: enabled (X-Ray destination = CloudWatchLogs)${RESET}"
  else
    echo "${YELLOW}Transaction Search is NOT enabled (destination=${dest}).${RESET}"
    echo "${YELLOW}Enable it once per account, then re-run this demo:${RESET}"
    cat <<'EOF'
  aws logs put-resource-policy --policy-name AgentCoreTransactionSearch \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Sid":"TransactionSearchXRayAccess",
    "Effect":"Allow","Principal":{"Service":"xray.amazonaws.com"},"Action":"logs:PutLogEvents",
    "Resource":["arn:aws:logs:REGION:ACCOUNT:log-group:aws/spans:*",
    "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/application-signals/data:*"],
    "Condition":{"ArnLike":{"aws:SourceArn":"arn:aws:xray:REGION:ACCOUNT:*"},
    "StringEquals":{"aws:SourceAccount":"ACCOUNT"}}}]}'
  aws xray update-trace-segment-destination --destination CloudWatchLogs
  aws xray update-indexing-rule --name "Default" --rule '{"Probabilistic":{"DesiredSamplingPercentage":100}}'
EOF
    exit 1
  fi
}

# ── scaffold: empty project + harness + the aws-targets.json (generated, not committed) ─
scaffold() {
  step "Step 1: Scaffold project and add the harness"
  rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"; cd "$WORKDIR"

  run agentcore create --project-name "$PROJECT_NAME" --no-agent

  # aws-targets.json needs the real account to deploy, so we GENERATE it here at
  # runtime. It is git-ignored — the sample ships this script, not your account id.
  say "Generating agentcore/aws-targets.json (account detected at runtime, not committed):"
  cat > "$PROJECT_NAME/agentcore/aws-targets.json" <<EOF
[
  { "name": "default", "description": "Observability demo", "account": "${ACCOUNT_ID}", "region": "${REGION}" }
]
EOF
  # Show it masked so the recording never reveals the account.
  echo "${GREEN}\$ cat agentcore/aws-targets.json${RESET}"
  mask < "$PROJECT_NAME/agentcore/aws-targets.json"

  cd "$PROJECT_NAME"
  run agentcore add harness \
    --name "$HARNESS_NAME" \
    --model-provider bedrock \
    --model-id "$MODEL_ID" \
    --system-prompt "$(cat "${DEMO_DIR}/system-prompt.md")"
}

# ── deploy: CDK builds the role; the harness is created; poll READY ────────────
deploy() {
  step "Step 2: Deploy (CDK creates the IAM execution role + the harness)"
  cd "$WORKDIR/$PROJECT_NAME"
  AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" run agentcore deploy --target default
  say "Harness status:"
  AWS_REGION="$REGION" run agentcore status --target default
}

# ── invoke: generate telemetry across one session ─────────────────────────────
invoke() {
  step "Step 3: Invoke the harness (one session, multiple turns)"
  cd "$WORKDIR/$PROJECT_NAME"
  say "Session ID: ${SESSION_ID}"
  local prompts=(
    "Introduce yourself in one sentence."
    "List three signals AgentCore Observability captures for an agent invocation."
    "What is the difference between a trace and a span?"
  )
  for p in "${prompts[@]}"; do
    echo; say "── turn ──"
    AWS_REGION="$REGION" run agentcore invoke --harness "$HARNESS_NAME" --session-id "$SESSION_ID" "$p"
  done
}

# ── verify: prove OpenTelemetry spans reached aws/spans ────────────────────────
verify() {
  step "Step 4: Verify OpenTelemetry spans in CloudWatch (aws/spans)"
  local start; start=$(( ($(date +%s) - 1800) * 1000 ))
  echo "${GREEN}\$ aws logs filter-log-events --log-group-name aws/spans \\
      --filter-pattern \"${SESSION_ID}\" --region ${REGION}${RESET}"

  # span_count: query aws/spans and count events for THIS session. We capture raw
  # JSON and count in python so the value is a single clean integer (the aws
  # --query 'length(events)' path can emit multi-line output across pages).
  span_count() {
    aws logs filter-log-events --log-group-name aws/spans \
        --start-time "$start" --filter-pattern "$SESSION_ID" \
        --region "$REGION" --output json 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("events",[])))' 2>/dev/null \
      || echo 0
  }

  # Spans take 1-3 min to appear; retry a few times instead of failing early.
  local count=0 attempt
  for attempt in 1 2 3 4; do
    say "Checking for spans (attempt ${attempt}/4)..."
    sleep 45
    count="$(span_count | tr -dc '0-9')"; count="${count:-0}"
    (( count > 0 )) && break
  done

  echo "OpenTelemetry span events for this session: ${BOLD}${count}${RESET}"
  if (( count > 0 )); then
    echo "${GREEN}✓ Traces are flowing into aws/spans.${RESET}"
  else
    echo "${YELLOW}No spans yet — give it a few more minutes (propagation lag).${RESET}"
    echo "${YELLOW}The Agent Inspector (next step) will keep polling and show them when they arrive.${RESET}"
  fi
}

# ── inspect: launch the Agent Inspector to view the telemetry interactively ────
# This is the point of the sample. The harness is deployed and has emitted traces
# (Step 4); now `agentcore dev` opens the Agent Inspector web UI so you can chat
# with the agent and watch its sessions / traces / spans live. We pass
# --skip-deploy because Step 2 already deployed (otherwise `dev` would redeploy).
inspect() {
  step "Step 5: Launch the Agent Inspector (agentcore dev)"
  cd "$WORKDIR/$PROJECT_NAME"
  echo "The Agent Inspector opens a local web UI wired to your deployed harness:"
  echo "  • a chat panel to invoke the agent"
  echo "  • a Traces pane that reads the same CloudWatch spans from Step 4"
  echo "  • a Memory/Resources view"
  echo
  echo "It runs until you stop it with Ctrl-C. You can also view the same data in"
  echo "the GenAI Observability console:"
  echo "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#gen-ai-observability"
  echo
  echo "${BOLD}Starting the Agent Inspector... (Ctrl-C to stop, then run ./cleanup.sh)${RESET}"
  echo
  # --skip-deploy: we already deployed in Step 2.  This is interactive and blocks
  # until you Ctrl-C — that is expected; it's the inspector you explore live.
  echo "${GREEN}\$ agentcore dev --skip-deploy${RESET}" | mask
  AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" agentcore dev --skip-deploy 2>&1 | mask
}

# ── self-test: offline validation (no AWS calls), for CI / pre-commit ──────────
self_test() {
  echo "Running offline self-test (no AWS calls)..."
  # Synthetic 12-digit ids (repeated digits) — fake test data, not real accounts.
  local fake_a; fake_a="$(printf '1%.0s' {1..12})"   # 111111111111
  local fake_b; fake_b="$(printf '2%.0s' {1..12})"   # 222222222222
  ACCOUNT_ID="$fake_a"
  # account id (explicit + stray 12-digit) -> <ACCOUNT>
  local out; out="$(echo "arn:aws:iam::${fake_a}:role/x and ${fake_b}" | mask)"
  [[ "$out" == "arn:aws:iam::<ACCOUNT>:role/x and <ACCOUNT>" ]] || { echo "FAIL: mask() account"; exit 1; }
  # username + home path -> <USER>
  local upath; upath="$(echo "Log: ${HOME}/x and user ${_OS_USER}" | mask)"
  [[ "$upath" != *"${_OS_USER}"* ]] || { echo "FAIL: mask() username leaked: $upath"; exit 1; }
  [[ -f "${DEMO_DIR}/system-prompt.md" ]] || { echo "FAIL: system-prompt.md missing"; exit 1; }
  echo "PASS: masking redacts account ids; system-prompt.md present."
}

# ── main ───────────────────────────────────────────────────────────────────────
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--self-test" ]]; then self_test; exit 0; fi

# ── banner: print exactly what this run will use (good for the recording) ──────
banner() {
  echo
  echo "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
  echo "${BOLD}${CYAN}║   AgentCore Harness — Getting started with Agent Inspector   ║${RESET}"
  echo "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"
  echo "  ${BOLD}Region   :${RESET} ${REGION}"
  echo "  ${BOLD}Project  :${RESET} ${PROJECT_NAME}"
  echo "  ${BOLD}Harness  :${RESET} ${HARNESS_NAME}"
  echo "  ${BOLD}Model    :${RESET} ${MODEL_ID}"
  echo "  ${BOLD}Run ID   :${RESET} ${RUN_ID}  ${DIM}(auto-generated — unique per run, no name collisions)${RESET}"
  echo "  ${DIM}Tear down later with: ./cleanup.sh   (no arguments needed)${RESET}"
  echo
}

# ── write_state: let cleanup.sh tear down THIS run with zero arguments ─────────
write_state() {
  cat > "$STATE_FILE" <<EOF
# Written by demo.sh — read by cleanup.sh. Safe to delete after teardown.
REGION="${REGION}"
PROJECT_NAME="${PROJECT_NAME}"
HARNESS_NAME="${HARNESS_NAME}"
WORKDIR="${WORKDIR}"
EOF
}

banner
preflight
write_state    # record what we're about to create so cleanup needs no args
scaffold
deploy
invoke
verify
inspect        # launch the Agent Inspector (interactive; Ctrl-C to stop)
