#!/usr/bin/env bash
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# End-to-end deploy: one command from a fresh clone to a signed-in console.
#
#   ./scripts/deploy.sh
#
# What it does, in order:
#   1. Checks the tools it needs are on PATH and Docker is running.
#   2. Loads AWS credentials from .env (and clears any stale AWS_PROFILE).
#   3. Confirms the account is entitled to the AgentCore Registry preview —
#      the one gate that fails an apply late, after ~50 resources exist.
#   4. Creates the local Python venv + frontend deps if missing (bootstrap.sh).
#   5. Ensures infra/terraform.tfvars exists and sets console_user_email —
#      without it the stack deploys but creates no console login.
#   6. terraform init, then apply. The first apply on a fresh account hits a
#      known provider quirk on the gateway target (metadata_configuration);
#      this retries apply once, which is the documented fix.
#   7. Wires the deployed resource IDs into the local env files (write_env.py).
#   8. Prints the console URL and login.
#
# The Terraform apply is what actually seeds everything: it builds and pushes
# both ARM64 images, creates the Gateway inference target, seeds the Registry
# records, and deploys the frontend — all through provisioners. The synthetic
# customer data ships inside the tool Lambda. So there is no separate seed step.
#
# Idempotent: safe to re-run. A second run is an incremental apply.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# ---- pretty output --------------------------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; RST=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; RST=""
fi
step() { echo; echo "${BOLD}==> $*${RST}"; }
ok()   { echo "    ${GRN}ok${RST}  $*"; }
warn() { echo "    ${YEL}!${RST}   $*"; }
die()  { echo "${RED}ERROR:${RST} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
step "Checking prerequisites"
# ---------------------------------------------------------------------------
missing=()
for tool in terraform aws node npm python3; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
[[ ${#missing[@]} -eq 0 ]] || die "not found on PATH: ${missing[*]}
  Need: Terraform >= 1.5, AWS CLI, Node 18+, Python 3.13, and a container engine
  (Docker or Finch)."

# Pick the container engine that will build and push the two ARM64 images.
# Docker and Finch both expose a Docker-compatible CLI; the Terraform build step
# (infra/modules/ecr-image) applies the buildx-only flags only when supported.
# Honour an explicit CONTAINER_CLI override; otherwise prefer whichever is
# actually running, Docker first.
engine_ready() { command -v "$1" >/dev/null 2>&1 && "$1" info >/dev/null 2>&1; }
CLI=""
if [[ -n "${CONTAINER_CLI:-}" ]]; then
  case "$CONTAINER_CLI" in
    docker|finch) ;;
    *) die "CONTAINER_CLI must be 'docker' or 'finch', got '${CONTAINER_CLI}'." ;;
  esac
  engine_ready "$CONTAINER_CLI" \
    || die "CONTAINER_CLI=${CONTAINER_CLI} is set but '${CONTAINER_CLI} info' fails."$'\n'"$(
         [[ "$CONTAINER_CLI" == finch ]] && echo "  Start it with: finch vm start" \
                                         || echo "  Start Docker Desktop." )"
  CLI="$CONTAINER_CLI"
else
  for candidate in docker finch; do
    if engine_ready "$candidate"; then CLI="$candidate"; break; fi
  done
  [[ -n "$CLI" ]] || die "no running container engine found (need Docker or Finch).
  Start Docker Desktop, or run 'finch vm start'. The apply builds two images.
  Force one with CONTAINER_CLI=docker|finch ./scripts/deploy.sh"
fi
# Terraform reads this as var.container_cli for the build/push step.
export TF_VAR_container_cli="$CLI"
ok "terraform, aws, node, npm, python3"
ok "container engine: ${CLI} (running)"

# ---------------------------------------------------------------------------
step "Loading AWS credentials from .env"
# ---------------------------------------------------------------------------
[[ -f .env ]] || die ".env is missing. Create it with:
  cat > .env <<'ENV'
  AWS_REGION=us-east-1
  AWS_ACCESS_KEY_ID=...
  AWS_SECRET_ACCESS_KEY=...
  ENV"

set -a
# shellcheck disable=SC1091
source .env
set +a
# A stale SSO profile would otherwise win over the static keys in .env.
unset AWS_PROFILE
REGION="${AWS_REGION:-us-east-1}"

CALLER="$(aws sts get-caller-identity --query Arn --output text 2>&1)" \
  || die "AWS credentials in .env are not valid: $CALLER"
ok "authenticated as ${CALLER}"
ok "region ${REGION}"

# ---------------------------------------------------------------------------
step "Checking Terraform state matches this account"
# ---------------------------------------------------------------------------
# Local state is not portable between accounts: the AWS provider derives each
# resource's identity from the CURRENT caller's account id, so reusing another
# account's state fails with "Unexpected Identity Change" before any API call.
# Deploying into a fresh account therefore needs fresh state — archive any state
# that belongs to a different account so this run starts clean. The old state is
# preserved under infra/.state-archive/<account>/ (restore it to manage that stack).
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
STATE="infra/terraform.tfstate"
if [[ -f "$STATE" && -n "$ACCOUNT" ]]; then
  # Pick the dominant account id among the ARNs in state.
  STATE_ACCT="$(grep -oE 'arn:aws[^"]*:[0-9]{12}:' "$STATE" 2>/dev/null \
    | grep -oE '[0-9]{12}' | sort | uniq -c | sort -rn | head -1 | awk '{print $2}' || true)"
  if [[ -n "$STATE_ACCT" && "$STATE_ACCT" != "$ACCOUNT" ]]; then
    # Timestamped so archiving a second stack from the same account never
    # clobbers an earlier archive.
    ARCHIVE="infra/.state-archive/${STATE_ACCT}-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$ARCHIVE"
    for f in "$STATE" "$STATE".backup "$STATE".*.backup; do
      [[ -e "$f" ]] && mv "$f" "$ARCHIVE"/
    done
    warn "state belonged to account ${STATE_ACCT}; archived to ${ARCHIVE}"
    warn "deploying FRESH into ${ACCOUNT} (restore that state file to manage the old stack)"
  else
    ok "state matches account ${ACCOUNT}"
  fi
else
  ok "no prior state — fresh deploy into ${ACCOUNT:-this account}"
fi

# ---------------------------------------------------------------------------
step "Checking AgentCore Registry preview entitlement"
# ---------------------------------------------------------------------------
# Registry is per-account enrolled — even AdministratorAccess is denied until
# allowlisted, and the apply would otherwise fail at CreateRegistry after
# provisioning most of the stack. Gateway, Memory, and Policy have no such gate.
if aws bedrock-agentcore-control list-registries --region "$REGION" >/dev/null 2>&1; then
  ok "account is entitled to the Registry preview"
else
  die "account is NOT entitled to the AgentCore Registry preview in ${REGION}.
  'aws bedrock-agentcore-control list-registries --region ${REGION}' returns AccessDenied.
  Request preview access from AWS, then re-run. Nothing in code changes this —
  it is granted at the service level. (Gateway/Memory/Policy are not gated.)"
fi

# ---------------------------------------------------------------------------
step "Preparing the local environment"
# ---------------------------------------------------------------------------
if [[ -x .venv/bin/python && -d frontend/node_modules ]]; then
  ok ".venv and frontend deps present"
else
  warn "running scripts/bootstrap.sh (first-time setup)"
  ./scripts/bootstrap.sh
fi

# ---------------------------------------------------------------------------
step "Checking deployment config (infra/terraform.tfvars)"
# ---------------------------------------------------------------------------
TFVARS="infra/terraform.tfvars"
if [[ ! -f "$TFVARS" ]]; then
  cp infra/terraform.tfvars.example "$TFVARS"
  warn "created ${TFVARS} from the example"
fi
# console_user_email must be set to SOME address, or no console login is created
# and you cannot sign in. Any valid address works — Cognito creates the user with
# a permanent password (no email delivery), so even the example address yields a
# working login. Block only when there is no uncommented value; nudge, but do not
# block, when it is still the example placeholder.
EMAIL_LINE="$(grep -E '^[[:space:]]*console_user_email[[:space:]]*=' "$TFVARS" 2>/dev/null | tail -1 || true)"
# Extract the quoted value with portable bash (BSD sed lacks \s): drop everything
# up to the first quote, then everything from the closing quote on.
EMAIL_VALUE="${EMAIL_LINE#*\"}"
EMAIL_VALUE="${EMAIL_VALUE%%\"*}"
if [[ -z "$EMAIL_VALUE" ]]; then
  die "set console_user_email in ${TFVARS} to an email address.
  Without it the stack deploys but creates no console login, so you cannot sign in.
  (Edit the line: console_user_email = \"you@example.com\")"
elif [[ "$EMAIL_VALUE" == "admin@example.com" ]]; then
  warn "console_user_email is still the example (admin@example.com) — it will work"
  warn "as a login, but set your own address in ${TFVARS} for a real deployment"
else
  ok "console_user_email = ${EMAIL_VALUE}"
fi

# ---------------------------------------------------------------------------
step "terraform init"
# ---------------------------------------------------------------------------
terraform -chdir=infra init -input=false >/dev/null
ok "initialized"

# ---------------------------------------------------------------------------
step "terraform apply  (builds images, seeds Registry, deploys frontend)"
# ---------------------------------------------------------------------------
# The first apply on a fresh account fails on the gateway target with "Provider
# produced inconsistent result after apply … metadata_configuration": the
# service injects a reserved x-amzn-* header the provider cannot reconcile at
# create time. The target IS created despite the error — but the failed create
# leaves it TAINTED, so a naive re-apply tries to *replace* it, hits the same
# inconsistency, and re-taints it. It never converges that way.
#
# The fix is to untaint the target between attempts: once untainted, the
# resource's `lifecycle.ignore_changes = [metadata_configuration]` keeps the
# service-injected header out of the plan, so the next apply leaves it alone and
# creates everything downstream of it. Retry a few times, untainting first.
apply() { terraform -chdir=infra apply -input=false -auto-approve; }

# Untaint every gateway target the create-time metadata_configuration
# inconsistency left tainted (a known provider bug). Generalized to ALL targets —
# the base SigV4 target and any gated OAuth gateway's target — and idempotent:
# untaint on an absent or clean resource is a swallowed no-op.
untaint_gateway_targets() {
  local addr
  while IFS= read -r addr; do
    [[ -z "$addr" ]] && continue
    terraform -chdir=infra untaint "$addr" >/dev/null 2>&1 \
      && warn "untainted ${addr##*.} (create-time inconsistency; see docs/preview-api-notes.md)" || true
  done < <(terraform -chdir=infra state list 2>/dev/null \
    | grep -E 'aws_bedrockagentcore_gateway_target\.' || true)
}

# On a fresh account the first apply hits the gateway-target inconsistency, and
# IAM-propagation / policy-engine-attach races can take a couple more rounds to
# settle. Untaint before every attempt (a tainted target would otherwise be
# replaced, re-hitting the inconsistency) and pause between retries so those
# races resolve. The count is generous because retries are cheap once most
# resources already exist.
MAX_APPLIES=6
applied=""
for attempt in $(seq 1 "$MAX_APPLIES"); do
  untaint_gateway_targets
  if [[ "$attempt" -gt 1 ]]; then
    warn "apply attempt ${attempt}/${MAX_APPLIES} (letting IAM/service state settle)"
    sleep 20
  fi
  if apply; then
    ok "apply complete$([[ $attempt -gt 1 ]] && echo " (attempt ${attempt})")"
    applied=1
    break
  fi
done
if [[ -z "$applied" ]]; then
  # A trailing untaint so a manual re-run isn't sabotaged by a left-behind taint.
  untaint_gateway_targets
  die "apply did not converge after ${MAX_APPLIES} attempts.
  Inspect the output above; docs/deployment.md covers the known failure modes
  (model access, Registry entitlement, identity change)."
fi

# ---------------------------------------------------------------------------
step "Wiring local env files (scripts/write_env.py)"
# ---------------------------------------------------------------------------
# Lets you run the console locally against the deployed backend if you want to.
python3 scripts/write_env.py >/dev/null
ok "wrote .env.deploy + frontend/.env.local"

# ---------------------------------------------------------------------------
step "Harness skill smoke test"
# ---------------------------------------------------------------------------
# The KYC agent skill ships in S3 (infra/harness.tf) but is attached per-call,
# not on the harness resource — the provider can't read back a non-path skill
# (see docs/platform-mechanics.md). So a deploy can't prove the skill works via
# apply alone; this runs one skill-attached invocation to close that gap.
#
# Non-fatal by design: it spends model tokens and can hit per-account model
# access limits, neither of which should fail an otherwise-good deploy. Skips
# cleanly when the harness is disabled. Set SKIP_HARNESS_SKILL_SMOKE=1 to opt out.
HARNESS_ARN="$(terraform -chdir=infra output -raw harness_arn 2>/dev/null || true)"
SKILL_URI="$(terraform -chdir=infra output -raw harness_skill_s3_uri 2>/dev/null || true)"
if [[ "${SKIP_HARNESS_SKILL_SMOKE:-0}" == "1" ]]; then
  warn "skipped (SKIP_HARNESS_SKILL_SMOKE=1)"
elif [[ -z "$HARNESS_ARN" || "$HARNESS_ARN" == "null" || -z "$SKILL_URI" || "$SKILL_URI" == "null" ]]; then
  warn "no harness deployed (enable_harness=false) — nothing to test"
elif [[ ! -x .venv/bin/python ]]; then
  warn ".venv/bin/python missing — cannot run the skill test (needs recent boto3)"
else
  # manage_harness_skill.py invokes the harness with the S3 skill and prints the
  # tools the loop called (look for `skills`) plus the verdict. Never fail the deploy.
  if .venv/bin/python scripts/manage_harness_skill.py \
       --region "$REGION" --harness-arn "$HARNESS_ARN" --s3-uri "$SKILL_URI"; then
    ok "skill loaded and the harness returned a verdict"
  else
    warn "skill smoke test did not complete (often per-account model access) — deploy is otherwise fine"
  fi
fi

# ---------------------------------------------------------------------------
step "Deployed. Sign in to the console:"
# ---------------------------------------------------------------------------
tf_out() { terraform -chdir=infra output -raw "$1" 2>/dev/null || echo "(unavailable)"; }
echo
echo "  ${BOLD}URL${RST}       $(tf_out console_url)"
echo "  ${BOLD}Username${RST}  $(tf_out console_username)"
echo "  ${BOLD}Password${RST}  $(tf_out console_password)"
echo
echo "  ${DIM}Run the console locally against this backend instead:${RST}"
echo "  ${DIM}  AUTH_DISABLED=1 ./scripts/dev.sh   # http://localhost:5173${RST}"
echo "  ${DIM}Tear it all down with:  terraform -chdir=infra destroy${RST}"
echo
