#!/usr/bin/env bash
#
# Demo: OpenAI (GPT-OSS) harness on Amazon Bedrock AgentCore via the AgentCore CLI.
# OpenAI models are served THROUGH Amazon Bedrock, so the harness uses the `bedrock` provider
# and the execution role (IAM) handles auth — no API key, no credential resource.
# Prints each command, runs it, shows real output. The AWS account ID is masked as <ACCOUNT>
# everywhere, in commands and output (including inside ARNs).
#
# Prereqs:
#   - agentcore CLI on PATH         (npm install -g @aws/agentcore@0.30.0)
#   - AWS credentials for a preview region  (us-east-1 | us-west-2 | ap-southeast-2 | eu-central-1)
#   - Bedrock model access enabled for the OpenAI GPT-OSS model in your account
#
# Usage:
#   ./demo.sh                            # just run it
#   STEP_PAUSE=2 ./demo.sh               # add a 2s pause between steps (for recording)
#   MODEL_ID=openai.gpt-oss-120b-1:0 ./demo.sh   # use the larger model
#
# Note: each run uses a unique, timestamp-suffixed harness name so you can re-record
# without hitting the soft-delete name reservation. Resources are LEFT RUNNING;
# cleanup commands are printed at the end.

set -uo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
export AWS_DEFAULT_REGION="$REGION"
MODEL_ID="${MODEL_ID:-openai.gpt-oss-20b-1:0}"   # or openai.gpt-oss-120b-1:0

# ── Account (fetched silently, never displayed; masked in all output) ────────
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"
[ -n "$ACCOUNT" ] || { echo "Could not resolve AWS account. Check your credentials."; exit 1; }

# ── Pretty output + account masking ──────────────────────────────────────────
if [ -t 1 ]; then
  C_CMD=$'\033[1;36m'; C_NOTE=$'\033[1;33m'; C_OK=$'\033[1;32m'; C_OFF=$'\033[0m'
else
  C_CMD=; C_NOTE=; C_OK=; C_OFF=
fi
# Mask the account ID in any stream, line-buffered (works on macOS + Linux awk).
redact() { awk -v a="$ACCOUNT" '{ if (a != "") gsub(a, "<ACCOUNT>"); print; fflush() }'; }
# Wrap-tolerant mask: catches the account even when output (e.g. `status`) wraps an
# ARN across a newline. Buffers the whole output, so use only for short commands.
redact_wrap() { ACCT="$ACCOUNT" perl -0777 -pe 'my $p=join("\\s*", map { quotemeta } split //, $ENV{ACCT}); s/$p/<ACCOUNT>/g'; }
note() { printf '\n%s# %s%s\n' "$C_NOTE" "$*" "$C_OFF"; }
run()  { printf '\n%s$ %s%s\n' "$C_CMD" "${1//$ACCOUNT/<ACCOUNT>}" "$C_OFF"; eval "$1" 2>&1 | redact; }
run_wrap() { printf '\n%s$ %s%s\n' "$C_CMD" "${1//$ACCOUNT/<ACCOUNT>}" "$C_OFF"; eval "$1" 2>&1 | redact_wrap; }
pause() { [ "${STEP_PAUSE:-0}" != "0" ] && sleep "${STEP_PAUSE}"; return 0; }

# ── Unique, readable names per run ──────────────────────────────────────────
TS="$(date +%H%M%S)"
PROJECT="openaidemo${TS}"        # project name: alphanumeric only
HARNESS="openai_${TS}"          # harness name: letter + alphanumeric/underscore

node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }

note "AgentCore CLI version"
run "agentcore --version"
note "Region: ${REGION}   Account: <ACCOUNT>   Model: ${MODEL_ID}"
pause

note "OpenAI GPT-OSS models available in Bedrock"
run "aws bedrock list-foundation-models --by-provider openai --region ${REGION} --query 'modelSummaries[].modelId' --output text"
pause

# ── 1. Create an empty harness project ──────────────────────────────────────
note "1) Create an empty project (no code-based agent)"
WORKDIR="$(mktemp -d)"; cd "$WORKDIR"
run "agentcore create --project-name ${PROJECT} --no-agent"
cd "${PROJECT}"
pause

# ── 2. Add the harness — bedrock provider, OpenAI model, NO credential ──────
note "2) Add the harness — provider bedrock, OpenAI GPT-OSS model (no API key needed)"
run "agentcore add harness --name ${HARNESS} --model-provider bedrock --model-id ${MODEL_ID} --no-memory"
run "cat app/${HARNESS}/harness.json"
pause

# ── 3. Set the deployment target ────────────────────────────────────────────
note "3) Set the default deployment target (aws-targets.json)"
cat > agentcore/aws-targets.json <<EOF
[{"name":"default","account":"${ACCOUNT}","region":"${REGION}"}]
EOF
run "cat agentcore/aws-targets.json"
pause

# ── 4. Deploy ───────────────────────────────────────────────────────────────
note "4) Deploy to AWS"
run "agentcore deploy --yes"
pause

# ── 5. Invoke ───────────────────────────────────────────────────────────────
note "5) Invoke the harness (live OpenAI-on-Bedrock call)"
SID="$(uuidgen)"
run "agentcore invoke --harness ${HARNESS} --session-id ${SID} \"In one short sentence, what model are you?\""
pause

# ── 6. Status ───────────────────────────────────────────────────────────────
note "6) Show deployed resources"
run_wrap "agentcore status"

# ── Done ────────────────────────────────────────────────────────────────────
printf '\n%s✓ OpenAI (GPT-OSS) harness demo complete.%s\n' "$C_OK" "$C_OFF"
note "Resources left running. To clean up:"
printf '   cd %s/%s\n' "$WORKDIR" "$PROJECT"
printf '   agentcore remove harness --name %s --yes\n' "$HARNESS"
printf '   agentcore deploy --yes\n'
