#!/usr/bin/env bash
#
# Demo: Gemini harness on Amazon Bedrock AgentCore via the AgentCore CLI.
# Prints each command, runs it, and shows the real output — built for terminal recording.
# The AWS account ID is masked as <ACCOUNT> everywhere, in commands and output.
#
# Prereqs:
#   - agentcore CLI on PATH         (npm install -g @aws/agentcore@0.30.0)
#   - AWS credentials for a preview region  (us-east-1 | us-west-2 | ap-southeast-2 | eu-central-1)
#   - A Gemini API key. Resolved automatically (in order):
#       1. $GEMINI_API_KEY if exported
#       2. demo.env next to this script  (KEY=value; gitignored — never committed)
#       3. hidden prompt
#
# Usage:
#   ./demo.sh                            # just run it
#   STEP_PAUSE=2 ./demo.sh               # add a 2s pause between steps (for recording)
#
# Note: each run uses unique, timestamp-suffixed names so you can re-record without
# hitting the soft-delete name reservation. Resources are LEFT RUNNING; cleanup
# commands are printed at the end.

set -uo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
export AWS_DEFAULT_REGION="$REGION"
MODEL_ID="gemini-2.5-flash"

# Gemini API key resolution
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f "${SCRIPT_DIR}/demo.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "${SCRIPT_DIR}/demo.env"; set +a
fi
if [ -z "${GEMINI_API_KEY:-}" ]; then
  printf 'Enter your Gemini API key (hidden): '
  read -rs GEMINI_API_KEY; printf '\n'
  [ -n "$GEMINI_API_KEY" ] || { echo "No key entered. Aborting."; exit 1; }
fi
export GEMINI_API_KEY

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
# Wrap-tolerant mask: catches the account even when a command (e.g. `status`) wraps
# an ARN and splits the digits across a newline. Buffers the whole output, so use
# only for short, non-streaming commands.
redact_wrap() { ACCT="$ACCOUNT" perl -0777 -pe 'my $p=join("\\s*", map { quotemeta } split //, $ENV{ACCT}); s/$p/<ACCOUNT>/g'; }
note() { printf '\n%s# %s%s\n' "$C_NOTE" "$*" "$C_OFF"; }
# Print the command (account masked), then run it (real account), output masked.
run()  { printf '\n%s$ %s%s\n' "$C_CMD" "${1//$ACCOUNT/<ACCOUNT>}" "$C_OFF"; eval "$1" 2>&1 | redact; }
# Same, but for commands whose output may wrap the account across lines.
run_wrap() { printf '\n%s$ %s%s\n' "$C_CMD" "${1//$ACCOUNT/<ACCOUNT>}" "$C_OFF"; eval "$1" 2>&1 | redact_wrap; }
pause() { [ "${STEP_PAUSE:-0}" != "0" ] && sleep "${STEP_PAUSE}"; return 0; }

# ── Unique, readable names per run ──────────────────────────────────────────
TS="$(date +%H%M%S)"
PROJECT="gemdemo${TS}"            # project name: alphanumeric only
HARNESS="gemini_${TS}"           # harness name: letter + alphanumeric/underscore
CRED="gemini-key-${TS}"          # credential name: alphanumeric/hyphen/dot (no underscore)
ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:token-vault/default/apikeycredentialprovider/${CRED}"

node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }

note "AgentCore CLI version"
run "agentcore --version"
note "Region: ${REGION}   Account: <ACCOUNT>   Model: ${MODEL_ID}"
pause

# ── 1. Create an empty harness project ──────────────────────────────────────
note "1) Create an empty project (no code-based agent)"
WORKDIR="$(mktemp -d)"; cd "$WORKDIR"
run "agentcore create --project-name ${PROJECT} --no-agent"
cd "${PROJECT}"
pause

# ── 2. Store the Gemini API key in AgentCore Identity (value hidden) ─────────
note "2) Store the Gemini API key in AgentCore Identity (key value hidden)"
printf '\n%s$ agentcore add credential --type api-key --name %s --api-key "$GEMINI_API_KEY"%s\n' "$C_CMD" "$CRED" "$C_OFF"
agentcore add credential --type api-key --name "$CRED" --api-key "$GEMINI_API_KEY" 2>&1 | redact
pause

# ── 3. Add the Gemini harness (full credential ARN) ─────────────────────────
note "3) Add the harness — provider gemini, credential ARN"
run "agentcore add harness --name ${HARNESS} --model-provider gemini --model-id ${MODEL_ID} --api-key-arn \"${ARN}\" --no-memory"
run "cat app/${HARNESS}/harness.json"
pause

# ── 4. Set the deployment target ────────────────────────────────────────────
note "4) Set the default deployment target (aws-targets.json)"
cat > agentcore/aws-targets.json <<EOF
[{"name":"default","account":"${ACCOUNT}","region":"${REGION}"}]
EOF
run "cat agentcore/aws-targets.json"
pause

# ── 5. Deploy ───────────────────────────────────────────────────────────────
note "5) Deploy to AWS"
run "agentcore deploy --yes"
pause

# ── 6. Invoke ───────────────────────────────────────────────────────────────
note "6) Invoke the harness (live Gemini call)"
SID="$(uuidgen)"
run "agentcore invoke --harness ${HARNESS} --session-id ${SID} \"In one short sentence, what model are you and who trained you?\""
pause

# ── 7. Status ───────────────────────────────────────────────────────────────
note "7) Show deployed resources"
run_wrap "agentcore status"

# ── Done ────────────────────────────────────────────────────────────────────
printf '\n%s✓ Gemini harness demo complete.%s\n' "$C_OK" "$C_OFF"
note "Resources left running. To clean up:"
printf '   cd %s/%s\n' "$WORKDIR" "$PROJECT"
printf '   agentcore remove harness --name %s --yes\n' "$HARNESS"
printf '   agentcore remove credential --name %s --yes\n' "$CRED"
printf '   agentcore deploy --yes\n'
