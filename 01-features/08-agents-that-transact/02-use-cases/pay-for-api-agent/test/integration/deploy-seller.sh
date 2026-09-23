#!/usr/bin/env bash
# Deploy the Fun Facts x402 seller stack via AWS CDK.
#
# The Lambda is Node.js with pre-installed node_modules (same pattern as
# agentcore-payments sellers) so this script runs `npm install` inside
# seller/lambda/ before `cdk deploy` packages the asset.
#
# Prerequisites:
#   - AWS CLI v2 configured (aws configure)
#   - AWS CDK v2 installed (npm install -g aws-cdk)
#   - Node.js 20+ and npm
#   - Python 3.10+ with pip (for the CDK Python dependencies)
#
# Optional:
#   - SELLER_WALLET_ADDRESS=0x…            # EVM (Base Sepolia) payout wallet
#   - SELLER_SOLANA_WALLET_ADDRESS=…       # Solana (Devnet) payout wallet
#   - X402_FACILITATOR_URL=…               # Override facilitator (defaults to x402.org)
#
# Usage (from anywhere):
#   bash test/integration/deploy-seller.sh
#
# After deploy, copy the printed SellerApiUrl into .env as SELLER_API_URL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Scripts live at <use-case>/test/integration/ — ../../ resolves the
# use-case root, the anchor for seller/ and .env.
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LAMBDA_DIR="${USE_CASE_ROOT}/seller/lambda"
CDK_DIR="${USE_CASE_ROOT}/seller/cdk"

# Pull the payout wallets + region from .env so the values the notebook
# prompted for in §2 flow through to the CDK deploy. Shell-env vars
# already set on the current session take precedence.
if [ -f "${USE_CASE_ROOT}/.env" ]; then
    # Guard against unreplaced placeholders like "<ACCOUNT_ID>" — bash
    # would try to interpret `<ACCOUNT_ID>` as a redirection and error
    # out with "No such file or directory" when sourcing. Tell the user
    # cleanly what went wrong instead.
    if grep -q "<ACCOUNT_ID>" "${USE_CASE_ROOT}/.env"; then
        echo "❌ ${USE_CASE_ROOT}/.env still contains <ACCOUNT_ID> placeholders." >&2
        echo "   Run:  bash test/integration/setup-roles.sh" >&2
        echo "   (or re-run §2 in the notebook) before deploying." >&2
        exit 1
    fi
    set -a
    # shellcheck disable=SC1091
    source "${USE_CASE_ROOT}/.env"
    set +a
fi

REGION="${AWS_REGION:-us-west-2}"

echo "── Pay for API — Seller Deploy ────────────────────────────"
echo "Region:   ${REGION}"
echo "Lambda:   ${LAMBDA_DIR}"
echo "CDK:      ${CDK_DIR}"
echo ""

# ── 0. Wallet sanity check ──
warn=()
if [ -z "${SELLER_WALLET_ADDRESS:-}" ]; then
    warn+=("  • SELLER_WALLET_ADDRESS (EVM) — required for Base Sepolia payments")
fi
if [ -z "${SELLER_SOLANA_WALLET_ADDRESS:-}" ]; then
    warn+=("  • SELLER_SOLANA_WALLET_ADDRESS (Solana) — required for Solana Devnet payments")
fi
if [ ${#warn[@]} -gt 0 ]; then
    echo "⚠️  One or more payout wallets are not set:"
    for line in "${warn[@]}"; do
        echo "${line}"
    done
    echo ""
    echo "   Without a payout wallet for a given network the seller emits an"
    echo "   invalid 402 for that network and the agent cannot pay on it."
    echo "   At minimum you need SELLER_WALLET_ADDRESS for the §8 EVM run."
    echo ""
    echo "   Set the missing ones in .env and re-run this script, e.g.:"
    echo "     export SELLER_WALLET_ADDRESS=0xYourBaseSepoliaAddress"
    echo "     export SELLER_SOLANA_WALLET_ADDRESS=YourSolanaDevnetAddress"
    echo ""
    read -r -p "   Continue anyway? [y/N] " ok
    case "${ok}" in
        y|Y|yes|YES) ;;
        *) echo "   Aborted."; exit 1 ;;
    esac
    echo ""
fi

# ── 1. Install Lambda node_modules ──
echo "Installing Lambda node_modules..."
(cd "${LAMBDA_DIR}" && npm install --silent --omit=dev)

# ── 2. CDK Python venv ──
if [ ! -d "${CDK_DIR}/.venv" ]; then
    echo "Creating Python venv for CDK..."
    python3 -m venv "${CDK_DIR}/.venv"
fi
# shellcheck disable=SC1091
source "${CDK_DIR}/.venv/bin/activate"

echo "Installing CDK Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "${CDK_DIR}/requirements.txt"

# ── 3. Bootstrap (idempotent) ──
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region "${REGION}" >/dev/null 2>&1; then
    echo ""
    echo "Bootstrapping CDK for ${ACCOUNT_ID}/${REGION}..."
    (cd "${CDK_DIR}" && cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}")
fi

# ── 4. Deploy ──
echo ""
echo "Deploying AgentCorePaymentsFunFactsSellerStack..."
(cd "${CDK_DIR}" && cdk deploy --require-approval never --outputs-file ./outputs.json)

API_URL="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsFunFactsSellerStack"]["SellerApiUrl"])')"
EVM_WALLET="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsFunFactsSellerStack"]["SellerEvmWallet"])')"
SVM_WALLET="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsFunFactsSellerStack"]["SellerSolanaWallet"])')"

echo ""
echo "── Deploy Complete ─────────────────────────────────────────"
echo "✅ SellerApiUrl:        ${API_URL}"
echo "   EVM payout wallet:   ${EVM_WALLET}"
echo "   Solana payout wallet: ${SVM_WALLET}"
echo ""

# Upsert SELLER_API_URL into .env so §3/§5/§7 in the notebook pick it
# up automatically on the next load_dotenv() without the user editing
# by hand. Preserves comments and other lines.
ENV_FILE="${USE_CASE_ROOT}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    cp "${USE_CASE_ROOT}/env-sample.txt" "${ENV_FILE}"
fi
python3 - <<PY
import pathlib
path = pathlib.Path("${ENV_FILE}")
lines = path.read_text().splitlines() if path.exists() else []
out, replaced = [], False
for line in lines:
    if line.startswith("SELLER_API_URL="):
        out.append(f"SELLER_API_URL=${API_URL}")
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(f"SELLER_API_URL=${API_URL}")
path.write_text("\n".join(out) + "\n")
PY
echo "💾 .env updated: SELLER_API_URL=${API_URL}"
