#!/usr/bin/env bash
# start_demo.sh — one command to launch the demo console with everything correct.
#
#   bash demo/start_demo.sh
#
# - ensures a python3.13 venv with the right deps (boto3>=1.43 + durable SDK),
#   self-healing if /tmp pruned it
# - verifies AWS creds + that the deployed runtimes are READY
# - (re)publishes the runtime ARNs to SSM so the orchestrator resolves them
# - optionally clears AgentCore Memory for a fresh baseline (CLEAR_MEMORY=1)
# - starts the visualization server and prints the URL
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PORT="${PORT:-8792}"
VENV="${VENV:-/tmp/poc-venv}"

c(){ printf '\033[1;36m[demo]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[demo:err]\033[0m %s\n' "$*"; exit 1; }

# 1) venv (python3.13 — the durable SDK + boto3 1.43 need it; brew py3.14 has broken expat).
# Default $VENV lives under /tmp, which macOS prunes on reboot/overnight — that leaves a
# DANGLING bin/python symlink + a half-gutted site-packages, so checking "is the symlink
# there?" isn't enough. Probe that `import boto3` actually works; rebuild from scratch if not.
if ! "$VENV/bin/python" -c "import boto3" >/dev/null 2>&1; then
  c "venv at $VENV missing/broken — (re)building (python3.13)"
  rm -rf "$VENV"
  PY313="$(command -v python3.13 || echo /opt/homebrew/bin/python3.13)"
  [ -x "$PY313" ] || die "python3.13 not found — brew install python@3.13"
  "$PY313" -m venv "$VENV"
  "$VENV/bin/pip" -q install --upgrade pip >/dev/null
  "$VENV/bin/pip" -q install "boto3>=1.43" aws-durable-execution-sdk-python >/dev/null
fi
PYBIN="$VENV/bin/python"

# 2) creds + config
aws sts get-caller-identity >/dev/null 2>&1 || die "no AWS creds — refresh your session first"
[ -f deploy/config.env ] || die "deploy/config.env missing — has the stack been deployed?"
set -a; . deploy/config.env; set +a
ACCT="$(aws sts get-caller-identity --query Account --output text)"
[ "$ACCT" = "$AWS_ACCOUNT" ] || c "WARNING: creds account ($ACCT) != config ($AWS_ACCOUNT)"

# 3) verify runtimes READY + (re)publish ARNs to SSM (so orchestrator resolves current ARNs)
c "checking runtimes + syncing SSM…"
for pair in "coding_agent:$RT_CAGENT_CODING_AGENT_ID:$RT_CAGENT_CODING_AGENT_ARN" \
            "sandbox:$RT_CAGENT_SANDBOX_ID:$RT_CAGENT_SANDBOX_ARN" \
            "sandbox_swift:$RT_CAGENT_SANDBOX_SWIFT_ID:$RT_CAGENT_SANDBOX_SWIFT_ARN" \
            "evaluator:$RT_CAGENT_EVALUATOR_ID:$RT_CAGENT_EVALUATOR_ARN"; do
  key="${pair%%:*}"; rest="${pair#*:}"; rid="${rest%%:*}"; arn="${rest#*:}"
  st="$("$PYBIN" -c "import boto3;print(boto3.client('bedrock-agentcore-control','$AWS_REGION').get_agent_runtime(agentRuntimeId='$rid')['status'])" 2>/dev/null || echo MISSING)"
  printf "    %-14s %s\n" "$key" "$st"
  [ "$st" = "READY" ] || c "    (warning: $key not READY — demo may stall on that stage)"
  aws ssm put-parameter --name "/${PROJECT}/runtime/${key}" --type String --value "$arn" --overwrite >/dev/null 2>&1 || true
done

# 4) optionally clear AgentCore Memory for a clean baseline (off by default so a relaunch
#    mid-demo never wipes the lessons you're showing). Enable with: CLEAR_MEMORY=1 ./demo/start_demo.sh
if [ "${CLEAR_MEMORY:-0}" = "1" ]; then
  c "clearing AgentCore Memory (CLEAR_MEMORY=1)…"
  "$PYBIN" demo/clear_memory.py || c "    (memory clear reported an issue — see above; continuing)"
else
  c "keeping existing Memory lessons (set CLEAR_MEMORY=1 to wipe for a fresh baseline)"
fi

# 5) launch
c "starting console on http://localhost:${PORT}  (account $AWS_ACCOUNT, $AWS_REGION)"
c "  → Fire Ticket 1, watch it flow; click components for logs; click Coding Agent for reasoning."
c "  → Use a FRESH ticket id per live run (the buttons cycle RAINBOW-1/2; edit index.html for more)."
exec env PORT="$PORT" "$PYBIN" demo/serve.py
