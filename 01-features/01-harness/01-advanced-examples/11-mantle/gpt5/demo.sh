#!/usr/bin/env bash
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# demo.sh — Deploy an AgentCore Harness that runs OpenAI GPT-5.4
# through the Amazon Bedrock "Mantle" endpoint (the OpenAI-compatible Responses
# API), and prove the run is observable in CloudWatch.
#
# This is the same flow as demo-mantle.sh, pointed at GPT-5.4 instead of the
# open-weight gpt-oss-120b. The harness model uses apiFormat: responses. With a
# Bedrock provider, that routes inference through the bedrock-mantle endpoint
# (OpenAI-compatible) instead of bedrock-runtime. No API key is configured on the
# harness: the execution role's Bedrock permissions are used.
#
# This echoes every command, masks your account id as <ACCOUNT> and your
# username/home path as <USER>, and auto-generates a unique name per run.
#
# Usage:
#   ./demo.sh                 # uses AWS_REGION or defaults to us-east-1
#   ./demo.sh --self-test     # offline checks only (no AWS calls)
#
# Tear down afterwards with: ./cleanup.sh  (reads the same .demo-state file)

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
REGION="${AWS_REGION:-us-east-1}"
RUN_ID="${RUN_ID:-$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]' | cut -c1-6)}"
PROJECT_NAME="${PROJECT_NAME:-gpt54${RUN_ID}}"       # <=23 chars, alphanumeric, starts with a letter
HARNESS_NAME="${HARNESS_NAME:-gpt54_${RUN_ID}}"      # <=48 chars, starts with a letter
# Mantle: a Bedrock-hosted OpenAI model + the OpenAI-compatible Responses API.
# GPT-5.4 supports the Responses API only (not Converse / Chat Completions), so
# apiFormat MUST be "responses" here.
MODEL_ID="${MODEL_ID:-openai.gpt-5.4}"
API_FORMAT="${API_FORMAT:-responses}"                # GPT-5.x: responses only
WORKDIR="${WORKDIR:-$(pwd)/.demo-workspace-${PROJECT_NAME}}"
SESSION_ID="gpt54-demo-$(uuidgen | tr '[:upper:]' '[:lower:]')"   # >= 33 chars
STATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.demo-state"

# ── Colors (disabled when not a TTY) ───────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; CYAN=""; GREEN=""; YELLOW=""; DIM=""; RESET=""
fi

ACCOUNT_ID=""

# ── mask: scrub account id + username/home path from displayed text ────────────
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

say()  { echo "${DIM}$*${RESET}" | mask; }
step() { echo; echo "${BOLD}${CYAN}=== $* ===${RESET}"; }
run() {
  echo "${GREEN}\$ $*${RESET}" | mask
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

  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  echo "${GREEN}\$ aws sts get-caller-identity --query Account --output text${RESET}"
  echo "<ACCOUNT>"
  say "Region: ${REGION}  |  Model: ${MODEL_ID}  |  API format: ${API_FORMAT} (Mantle)"

  # Transaction Search — the one account-level prerequisite for trace visibility.
  local dest
  dest="$(aws xray get-trace-segment-destination --region "$REGION" \
            --query Destination --output text 2>/dev/null || echo UNKNOWN)"
  if [[ "$dest" == "CloudWatchLogs" ]]; then
    echo "${GREEN}Transaction Search: enabled (X-Ray destination = CloudWatchLogs)${RESET}"
  else
    echo "${YELLOW}Transaction Search is NOT enabled (destination=${dest}). Enable it once per account:${RESET}"
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

# ── scaffold: empty project + GPT-5.4 Mantle harness ───────────────────────────
scaffold() {
  step "Step 1: Scaffold project and add a GPT-5.4 Mantle harness"
  rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"; cd "$WORKDIR"

  run agentcore create --project-name "$PROJECT_NAME" --no-agent

  say "Generating agentcore/aws-targets.json (account detected at runtime, not committed):"
  cat > "$PROJECT_NAME/agentcore/aws-targets.json" <<EOF
[
  { "name": "default", "description": "GPT-5.4 Mantle harness demo", "account": "${ACCOUNT_ID}", "region": "${REGION}" }
]
EOF
  echo "${GREEN}\$ cat agentcore/aws-targets.json${RESET}"
  mask < "$PROJECT_NAME/agentcore/aws-targets.json"

  cd "$PROJECT_NAME"
  # --api-format responses routes a bedrock-provider model through the
  # bedrock-mantle (OpenAI-compatible) endpoint. GPT-5.4 supports the Responses
  # API only, so responses is the correct (and required) format here.
  run agentcore add harness \
    --name "$HARNESS_NAME" \
    --model-provider bedrock \
    --model-id "$MODEL_ID" \
    --api-format "$API_FORMAT" \
    --system-prompt "$(cat "${DEMO_DIR}/system-prompt.md")"

  say "harness.json now carries the Mantle apiFormat:"
  echo "${GREEN}\$ cat app/${HARNESS_NAME}/harness.json${RESET}"
  mask < "app/${HARNESS_NAME}/harness.json"
}

deploy() {
  step "Step 2: Deploy (CDK creates the IAM execution role + the harness)"
  cd "$WORKDIR/$PROJECT_NAME"
  AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" run agentcore deploy --target default
  say "Harness status:"
  AWS_REGION="$REGION" run agentcore status --target default
}

invoke() {
  step "Step 3: Invoke the GPT-5.4 harness through Mantle (one session, multiple turns)"
  cd "$WORKDIR/$PROJECT_NAME"
  say "Session ID: ${SESSION_ID}"
  local prompts=(
    "In one sentence, what is an AI agent harness?"
    "List three signals AgentCore Observability captures for an agent invocation."
    "In one sentence, what is the difference between a trace and a span?"
  )
  for p in "${prompts[@]}"; do
    echo; say "── turn ──"
    AWS_REGION="$REGION" run agentcore invoke --harness "$HARNESS_NAME" --session-id "$SESSION_ID" "$p"
  done
}

# ── verify: prove spans reached aws/spans AND carry the Mantle model id ─────────
verify() {
  step "Step 4: Verify OpenTelemetry spans in CloudWatch (aws/spans)"
  local start; start=$(( ($(date +%s) - 1800) * 1000 ))
  echo "${GREEN}\$ aws logs filter-log-events --log-group-name aws/spans \\
      --filter-pattern \"${SESSION_ID}\" --region ${REGION}${RESET}"

  fetch_spans() {
    aws logs filter-log-events --log-group-name aws/spans \
        --start-time "$start" --filter-pattern "$SESSION_ID" \
        --region "$REGION" --output json 2>/dev/null
  }

  local count=0 attempt raw=""
  for attempt in 1 2 3 4; do
    say "Checking for spans (attempt ${attempt}/4)..."
    sleep 45
    raw="$(fetch_spans || echo '{}')"
    count="$(echo "$raw" | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("events",[])))' 2>/dev/null || echo 0)"
    count="$(echo "$count" | tr -dc '0-9')"; count="${count:-0}"
    (( count > 0 )) && break
  done

  echo "OpenTelemetry span events for this session: ${BOLD}${count}${RESET}"
  if (( count > 0 )); then
    echo "${GREEN}✓ The GPT-5.4 Mantle harness is observable — spans are flowing into aws/spans.${RESET}"
    local model
    model="$(echo "$raw" | python3 -c '
import sys, json
ev = json.load(sys.stdin).get("events", [])
for e in ev:
    try:
        m = json.loads(e["message"]).get("attributes", {}).get("gen_ai.request.model")
        if m:
            print(m); break
    except Exception:
        pass
' 2>/dev/null || echo "")"
    if [[ -n "$model" ]]; then
      echo "${GREEN}✓ gen_ai.request.model = ${BOLD}${model}${RESET} ${DIM}(served via bedrock-mantle, apiFormat=${API_FORMAT})${RESET}"
    else
      echo "${DIM}  (GenAI model spans may take a few more minutes; the Inspector keeps polling.)${RESET}"
    fi
  else
    echo "${YELLOW}No spans yet — give it a few more minutes (propagation lag).${RESET}"
  fi
}

inspect() {
  step "Step 5: Launch the Agent Inspector (agentcore dev)"
  cd "$WORKDIR/$PROJECT_NAME"
  echo "The Agent Inspector opens a local web UI wired to your deployed GPT-5.4 harness:"
  echo "  • a chat panel to invoke the agent"
  echo "  • a Traces pane (the same CloudWatch spans from Step 4)"
  echo "  • a Memory/Resources view"
  echo
  echo "GenAI Observability console:"
  echo "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#gen-ai-observability"
  echo
  echo "${BOLD}Starting the Agent Inspector... (Ctrl-C to stop, then run ./cleanup.sh)${RESET}"
  echo
  echo "${GREEN}\$ agentcore dev --skip-deploy${RESET}" | mask
  AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" agentcore dev --skip-deploy 2>&1 | mask
}

self_test() {
  echo "Running offline self-test (no AWS calls)..."
  local fake_a; fake_a="$(printf '1%.0s' {1..12})"
  local fake_b; fake_b="$(printf '2%.0s' {1..12})"
  ACCOUNT_ID="$fake_a"
  local out; out="$(echo "arn:aws:iam::${fake_a}:role/x and ${fake_b}" | mask)"
  [[ "$out" == "arn:aws:iam::<ACCOUNT>:role/x and <ACCOUNT>" ]] || { echo "FAIL: mask() account"; exit 1; }
  local upath; upath="$(echo "Log: ${HOME}/x and user ${_OS_USER}" | mask)"
  [[ "$upath" != *"${_OS_USER}"* ]] || { echo "FAIL: mask() username leaked: $upath"; exit 1; }
  [[ -f "${DEMO_DIR}/system-prompt.md" ]] || { echo "FAIL: system-prompt.md missing"; exit 1; }
  case "$API_FORMAT" in
    converse_stream|responses|chat_completions) ;;
    *) echo "FAIL: API_FORMAT must be converse_stream|responses|chat_completions"; exit 1;;
  esac
  echo "PASS: masking + files + API_FORMAT (${API_FORMAT}) valid."
}

# ── main ───────────────────────────────────────────────────────────────────────
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--self-test" ]]; then self_test; exit 0; fi

banner() {
  echo
  echo "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
  echo "${BOLD}${CYAN}║   AgentCore Harness — GPT-5.4 via Mantle (OpenAI Responses)  ║${RESET}"
  echo "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"
  echo "  ${BOLD}Region    :${RESET} ${REGION}"
  echo "  ${BOLD}Project   :${RESET} ${PROJECT_NAME}"
  echo "  ${BOLD}Harness   :${RESET} ${HARNESS_NAME}"
  echo "  ${BOLD}Model     :${RESET} ${MODEL_ID}"
  echo "  ${BOLD}API format:${RESET} ${API_FORMAT}  ${DIM}(responses => bedrock-mantle endpoint)${RESET}"
  echo "  ${BOLD}Run ID    :${RESET} ${RUN_ID}  ${DIM}(auto-generated — unique per run)${RESET}"
  echo "  ${DIM}Tear down later with: ./cleanup.sh   (no arguments needed)${RESET}"
  echo
}

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
write_state
scaffold
deploy
invoke
verify
inspect
