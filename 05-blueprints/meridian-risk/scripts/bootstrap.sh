#!/usr/bin/env bash
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Create the local Python environment the deploy scripts and console API need.
#
# AgentCore Registry is a preview API, so a recent boto3 is required — system
# boto3 typically does not yet know the bedrock-agentcore service models.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

echo "==> Creating .venv"
if command -v uv >/dev/null 2>&1; then
  uv venv .venv --python 3.13
  uv pip install --python .venv -r backend/api/requirements.txt
else
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r backend/api/requirements.txt
fi

echo "==> Verifying the preview AgentCore APIs are available"
./.venv/bin/python - <<'PY'
import sys
import boto3

for service in ("bedrock-agentcore", "bedrock-agentcore-control"):
    try:
        boto3.client(service, region_name="us-east-1")
    except Exception as exc:
        print(f"FAIL {service}: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  ok  {service}")
print(f"  boto3 {boto3.__version__}")
PY

echo "==> Installing frontend dependencies"
(cd frontend && npm install --silent)

echo
echo "Bootstrap complete. Next (see README 'Deploy' for details):"
echo "  1. cp infra/terraform.tfvars.example infra/terraform.tfvars"
echo "     then set console_user_email — without it no console login is created"
echo "  2. set -a && source .env && set +a && unset AWS_PROFILE"
echo "  3. terraform -chdir=infra init"
echo "  4. terraform -chdir=infra apply"
echo "  5. terraform -chdir=infra output -raw console_url   # then sign in"
echo
echo "  Optional — run the console locally against the deployed backend:"
echo "    python3 scripts/write_env.py && AUTH_DISABLED=1 ./scripts/dev.sh"
