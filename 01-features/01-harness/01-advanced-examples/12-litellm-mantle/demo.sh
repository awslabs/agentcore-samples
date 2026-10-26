#!/usr/bin/env bash
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# demo.sh — Deploy an AgentCore Harness that runs OpenAI GPT-5.4 through a LiteLLM
# model configuration, with LiteLLM routing inference to the Amazon Bedrock
# "Mantle" endpoint (the OpenAI-compatible Responses API). Then prove the run is
# observable in CloudWatch.
#
# How this differs from 11-mantle/gpt5:
#   - 09 uses --model-provider bedrock --api-format responses. The harness talks to
#     Mantle directly; no API key, the execution role's Bedrock access is used.
#   - This sample uses --model-provider lite_llm. LiteLLM is a routing layer (NOT a
#     model provider in the marketing sense). It needs a base URL (--api-base) and an
#     API key credential (--api-key-arn) to reach the endpoint. So this sample also
#     shows the AgentCore credential flow: `agentcore add credential` stores the key
#     in agentcore/.env.local (git-ignored), and `agentcore deploy` creates the
#     token-vault API-key credential provider from it.
#
# This echoes every command, masks your account id as <ACCOUNT> and your
# username/home path as <USER>, and auto-generates unique names per run.
#
# Usage:
#   BEDROCK_API_KEY=... ./demo.sh              # uses AWS_REGION or defaults to us-east-1
#   ./demo.sh --self-test                      # offline checks only (no AWS calls)
#
# The Bedrock API key is read from the BEDROCK_API_KEY environment variable (or a
# demo.env file next to this script: a line `BEDROCK_API_KEY=...`). It is NEVER
# printed and NEVER committed. Generate one in the Bedrock console under "API keys".
#
# Tear down afterwards with: ./cleanup.sh  (reads the same .demo-state file)

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
REGION="${AWS_REGION:-us-east-1}"
RUN_ID="${RUN_ID:-$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]' | cut -c1-6)}"
PROJECT_NAME="${PROJECT_NAME:-litellm${RUN_ID}}"        # <=23 chars, alphanumeric, starts with a letter
HARNESS_NAME="${HARNESS_NAME:-litellm_${RUN_ID}}"       # <=48 chars, starts with a letter
CRED_NAME="${CRED_NAME:-mantlekey${RUN_ID}}"            # token-vault credential provider name

# LiteLLM routing: GPT-5.4 over the Mantle Responses API.
#   modelId "openai/responses/openai.gpt-5.4" — the "responses/" segment forces LiteLLM
#     to use the OpenAI Responses route (GPT-5.4 supports Responses only, not Chat
#     Completions or Converse).
#   apiBase ".../openai/v1" — the OpenAI-compatible base path on the Mantle endpoint.
MODEL_ID="${MODEL_ID:-openai/responses/openai.gpt-5.4}"
API_BASE="${API_BASE:-https://bedrock-mantle.${REGION}.api.aws/openai/v1}"

WORKDIR="${WORKDIR:-$(pwd)/.demo-workspace-${PROJECT_NAME}}"
SESSION_ID="litellm-demo-$(uuidgen | tr '[:upper:]' '[:lower:]')"   # >= 33 chars
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${DEMO_DIR}/.demo-state"

# Keep the CLI from hard-wrapping long ARNs to 80 cols when its output is piped.
# A wrapped 12-digit account id would slip past the line-based mask() below.
export COLUMNS="${COLUMNS:-220}"

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

# ── load the Bedrock API key (never printed) ───────────────────────────────────
load_api_key() {
  if [[ -z "${BEDROCK_API_KEY:-}" && -f "${DEMO_DIR}/demo.env" ]]; then
    # only read a BEDROCK_API_KEY line; don't source arbitrary content.
    # strip an optional leading/trailing single or double quote around the value.
    BEDROCK_API_KEY="$(grep -E '^BEDROCK_API_KEY=' "${DEMO_DIR}/demo.env" | head -1 | cut -d= -f2- \
      | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
  fi
  if [[ -z "${BEDROCK_API_KEY:-}" ]]; then
    echo "${YELLOW}BEDROCK_API_KEY is not set.${RESET}"
    echo "  Generate a Bedrock API key (Bedrock console -> API keys), then either:"
    echo "    export BEDROCK_API_KEY=...     # in your shell"
    echo "    or put 'BEDROCK_API_KEY=...' in a demo.env file next to this script"
    exit 1
  fi
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
  say "Region: ${REGION}  |  Model: ${MODEL_ID}  |  Provider: lite_llm -> Mantle (${API_BASE})"

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

# ── scaffold: empty project + API-key credential + lite_llm harness ─────────────
scaffold() {
  step "Step 1: Scaffold project, add the API key credential, add the LiteLLM harness"
  rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"; cd "$WORKDIR"

  run agentcore create --project-name "$PROJECT_NAME" --no-agent --skip-git

  say "Generating agentcore/aws-targets.json (account detected at runtime, not committed):"
  cat > "$PROJECT_NAME/agentcore/aws-targets.json" <<EOF
[
  { "name": "default", "description": "LiteLLM -> Mantle GPT-5.4 harness demo", "account": "${ACCOUNT_ID}", "region": "${REGION}" }
]
EOF
  echo "${GREEN}\$ cat agentcore/aws-targets.json${RESET}"
  mask < "$PROJECT_NAME/agentcore/aws-targets.json"

  cd "$PROJECT_NAME"

  # The Bedrock API key the LiteLLM config uses to call Mantle. `add credential`
  # writes it to agentcore/.env.local (git-ignored); `deploy` creates the token-vault
  # API-key credential provider from it. The key value is not echoed here.
  say "Adding the Bedrock API key as a credential (value hidden; stored in agentcore/.env.local):"
  echo "${GREEN}\$ agentcore add credential --name ${CRED_NAME} --api-key '<hidden>' --type api-key${RESET}"
  agentcore add credential --name "$CRED_NAME" --api-key "$BEDROCK_API_KEY" --type api-key --json 2>&1 | mask

  local key_arn="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:token-vault/default/apikeycredentialprovider/${CRED_NAME}"

  # --model-provider lite_llm routes through LiteLLM; --api-base points LiteLLM at the
  # Mantle OpenAI-compatible endpoint; --api-key-arn binds the credential provider.
  # --no-memory keeps the sample minimal (memory is its own tutorial).
  run agentcore add harness \
    --name "$HARNESS_NAME" \
    --model-provider lite_llm \
    --model-id "$MODEL_ID" \
    --api-base "$API_BASE" \
    --api-key-arn "$key_arn" \
    --no-memory \
    --system-prompt "$(cat "${DEMO_DIR}/system-prompt.md")"

  say "harness.json now carries the LiteLLM model config:"
  echo "${GREEN}\$ cat app/${HARNESS_NAME}/harness.json${RESET}"
  mask < "app/${HARNESS_NAME}/harness.json"
}

deploy() {
  step "Step 2: Deploy (CDK creates the IAM role, the credential provider, and the harness)"
  cd "$WORKDIR/$PROJECT_NAME"
  AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" run agentcore deploy --target default --yes
  say "Harness status:"
  AWS_REGION="$REGION" run agentcore status --target default
}

invoke() {
  step "Step 3: Invoke the GPT-5.4 LiteLLM harness (one session, multiple turns)"
  cd "$WORKDIR/$PROJECT_NAME"
  say "Session ID: ${SESSION_ID}"

  # NOTE on --allowed-tools "none":
  # On the lite_llm -> Mantle Responses path, the runtime currently forwards a tool
  # definition whose schema is missing its 'name', and the Mantle Responses API
  # rejects the request ("Invalid 'tools': missing field name") — even though this
  # harness has no tools configured. Passing a non-empty allow-list that does not
  # match any real tool ("none") makes the runtime forward zero tools, so the request
  # is accepted. Read it as "allow no tools." This is a temporary workaround for a
  # known issue specific to the LiteLLM Responses path (the bedrock-provider Responses
  # path in 11-mantle/gpt5 does not need it); remove it once the runtime is fixed.
  local ALLOW=(--allowed-tools "none")

  local prompts=(
    "In one sentence, what is an AI agent harness?"
    "List three signals AgentCore Observability captures for an agent invocation."
    "In one sentence, what is the difference between a trace and a span?"
  )
  for p in "${prompts[@]}"; do
    echo; say "── turn ──"
    AWS_REGION="$REGION" run agentcore invoke --harness "$HARNESS_NAME" --session-id "$SESSION_ID" "${ALLOW[@]}" "$p"
  done
}

# ── verify: prove spans reached aws/spans AND carry the model id ────────────────
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
    echo "${GREEN}✓ The LiteLLM harness is observable — spans are flowing into aws/spans.${RESET}"
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
      echo "${GREEN}✓ gen_ai.request.model = ${BOLD}${model}${RESET} ${DIM}(routed by LiteLLM to bedrock-mantle)${RESET}"
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
  echo "The Agent Inspector opens a local web UI wired to your deployed harness:"
  echo "  • a chat panel to invoke the agent"
  echo "  • a Traces pane (the same CloudWatch spans from Step 4)"
  echo "  • a Resources view"
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
  case "$MODEL_ID" in
    */responses/*) ;;
    *) echo "FAIL: MODEL_ID should use the responses/ route for GPT-5.4 (got ${MODEL_ID})"; exit 1;;
  esac
  [[ "$API_BASE" == *"/openai/v1"* ]] || { echo "FAIL: API_BASE should end in /openai/v1 (got ${API_BASE})"; exit 1; }
  echo "PASS: masking + files + LiteLLM model/route (${MODEL_ID}) + apiBase valid."
}

# ── main ───────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--self-test" ]]; then self_test; exit 0; fi

banner() {
  echo
  echo "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
  echo "${BOLD}${CYAN}║   AgentCore Harness — GPT-5.4 via LiteLLM -> Mantle          ║${RESET}"
  echo "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"
  echo "  ${BOLD}Region    :${RESET} ${REGION}"
  echo "  ${BOLD}Project   :${RESET} ${PROJECT_NAME}"
  echo "  ${BOLD}Harness   :${RESET} ${HARNESS_NAME}"
  echo "  ${BOLD}Credential:${RESET} ${CRED_NAME}  ${DIM}(token-vault API-key provider)${RESET}"
  echo "  ${BOLD}Model     :${RESET} ${MODEL_ID}"
  echo "  ${BOLD}Provider  :${RESET} lite_llm  ${DIM}(routes to ${API_BASE})${RESET}"
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
CRED_NAME="${CRED_NAME}"
WORKDIR="${WORKDIR}"
EOF
}

banner
load_api_key
preflight
write_state
scaffold
deploy
invoke
verify
# inspect   # Agent Inspector (agentcore dev) — disabled in this recording.
#           # Its chat panel cannot pass --allowed-tools, so on the LiteLLM Responses
#           # path it hits the tool bug. View telemetry in the GenAI Observability
#           # console instead (link printed below). Re-enable once the runtime is fixed.
echo
echo "${BOLD}${CYAN}=== Done ===${RESET}"
echo "View the traces and spans in the CloudWatch GenAI Observability console:"
echo "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#gen-ai-observability"
echo "Tear down with: ./cleanup.sh"
