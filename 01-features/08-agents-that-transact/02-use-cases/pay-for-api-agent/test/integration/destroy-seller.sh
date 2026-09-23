#!/usr/bin/env bash
# Tear down the Fun Facts seller stack.
#
# Usage (from anywhere):
#   bash test/integration/destroy-seller.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Scripts live at <use-case>/test/integration/ — ../../ resolves the
# use-case root.
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CDK_DIR="${USE_CASE_ROOT}/seller/cdk"

if [ -d "${CDK_DIR}/.venv" ]; then
    # shellcheck disable=SC1091
    source "${CDK_DIR}/.venv/bin/activate"
fi

echo "Destroying AgentCorePaymentsFunFactsSellerStack..."
(cd "${CDK_DIR}" && cdk destroy --force)

echo ""
echo "✅ Seller stack destroyed."
