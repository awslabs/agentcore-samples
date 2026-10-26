#!/usr/bin/env bash
set -euo pipefail

# Keep common CLI locations on PATH for GUI-launched Jupyter/VS Code kernels
# (see setup-roles.sh) so aws / jq are found when run from a notebook.
export PATH="/usr/local/bin:/opt/homebrew/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SAMPLE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$SAMPLE_ROOT/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # Parse KEY=VALUE lines literally so unreplaced "<...>" placeholders can't be
  # interpreted as shell redirections (see setup-roles.sh for the rationale).
  while IFS= read -r _env_line || [[ -n "$_env_line" ]]; do
    [[ -z "$_env_line" || "$_env_line" == \#* || "$_env_line" != *=* ]] && continue
    export "${_env_line%%=*}=${_env_line#*=}"
  done < "$ENV_FILE"
fi

if [[ "${RUN_AWS_X402_E2E:-0}" != "1" ]]; then
  echo "AWS/x402 integration: SKIPPED - RUN_AWS_X402_E2E is not 1"
  exit 0
fi

die() {
  echo "ERROR: $*" >&2
  exit 1
}

command -v aws >/dev/null 2>&1 || die "aws CLI not found"
command -v jq >/dev/null 2>&1 || die "jq not found"

PAY_TO="${PAY_TO:?Missing PAY_TO. Use a dedicated sample payee address only.}"
PAYMENT_AMOUNT="${PAYMENT_AMOUNT:?Missing PAYMENT_AMOUNT. Use a small sample amount.}"
MAX_PAYMENT_AMOUNT_USDC="${MAX_PAYMENT_AMOUNT_USDC:-0.25}"
SESSION_MAX_SPEND="${SESSION_MAX_SPEND:-1.0}"
PAYMENT_CONNECTOR_ID="${PAYMENT_CONNECTOR_ID:?Missing PAYMENT_CONNECTOR_ID}"
MANAGER_ARN="${MANAGER_ARN:?Missing MANAGER_ARN}"
MANAGEMENT_ROLE_ARN="${MANAGEMENT_ROLE_ARN:?Missing MANAGEMENT_ROLE_ARN}"
PROCESS_PAYMENT_ROLE_ARN="${PROCESS_PAYMENT_ROLE_ARN:?Missing PROCESS_PAYMENT_ROLE_ARN}"

python - "$PAYMENT_AMOUNT" "$MAX_PAYMENT_AMOUNT_USDC" "$SESSION_MAX_SPEND" <<'PY'
from decimal import Decimal
import sys

amount = Decimal(sys.argv[1])
cap = Decimal(sys.argv[2])
session_max = Decimal(sys.argv[3])
if amount <= 0:
    raise SystemExit("PAYMENT_AMOUNT must be positive")
if amount > cap:
    raise SystemExit(f"PAYMENT_AMOUNT exceeds MAX_PAYMENT_AMOUNT_USDC={cap}")
if session_max <= 0:
    raise SystemExit("SESSION_MAX_SPEND must be positive")
if session_max < amount:
    raise SystemExit("SESSION_MAX_SPEND must be greater than or equal to PAYMENT_AMOUNT")
PY

CALLER_IDENTITY="$(aws sts get-caller-identity --output json)"
ACCOUNT_ID="$(echo "$CALLER_IDENTITY" | jq -r '.Account')"
if [[ -z "${CONFIRM_AWS_ACCOUNT_ID:-}" || "$CONFIRM_AWS_ACCOUNT_ID" != "$ACCOUNT_ID" ]]; then
  die "CONFIRM_AWS_ACCOUNT_ID does not match the current AWS caller. Update .env before running live integration checks."
fi

aws bedrock-agentcore help >/dev/null
aws bedrock-agentcore-control help >/dev/null

echo "AWS/x402 integration prerequisite gate passed."
echo "No live paid request is executed by this shell gate."
echo "Session max spend configured: ${SESSION_MAX_SPEND} USDC."
echo "Payment connector configured."
