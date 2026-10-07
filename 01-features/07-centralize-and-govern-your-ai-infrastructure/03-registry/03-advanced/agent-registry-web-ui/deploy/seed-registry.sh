#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Seed the live Agent Registry + Cognito persona users for the demo.
# Prereqs: the Cognito foundation exists (deploy/state/outputs.env populated) AND
# the 3 IAM roles + Identity Pool role attachment + groups exist (deploy/setup/01-foundation.sh).
# This script does NOT create IAM roles.
#
# The registry is created here, not from the UI: choosing the authorization model is
# an immutable, administrator-level decision. Set REGISTRY_AUTH_MODE=AWS_IAM to
# create a SigV4-authorized registry instead of the CUSTOM_JWT default.
#
# Idempotent-ish: safe to re-run; existing users/records may error (ignored).
#
# Password: set PERSONA_PASSWORD to reuse a chosen password, otherwise the script
# GENERATES a random policy-valid one, prints it once, and saves it to
# deploy/state/persona-password.txt (chmod 600, gitignored).
set -euo pipefail
# Region: honour the caller's AWS_REGION (setup/_lib.sh defaults it to us-east-1).
export AWS_REGION="${AWS_REGION:-us-east-1}"
D="$(cd "$(dirname "$0")" && pwd)"
ST="$D/state/outputs.env"
[ -f "$ST" ] || { echo "Missing $ST — deploy the Cognito foundation first."; exit 1; }
# shellcheck disable=SC1090
source "$ST"

# ---------------------------------------------------------------------------
# Resolve + VALIDATE the persona password BEFORE creating any AWS resource, so a
# bad password fails fast instead of leaving an orphaned registry behind.
# ---------------------------------------------------------------------------
if [ -z "${PERSONA_PASSWORD:-}" ]; then
  # Draw from each required class explicitly so the policy is always satisfied,
  # regardless of what the random body happens to contain. Avoid the
  # `tr < /dev/urandom | head` pipeline (SIGPIPE under pipefail can truncate it).
  gen_class() {  # gen_class <charset> <count>  -> prints <count> random chars from <charset>
    local set="$1" n="$2" out="" i len=${#1}
    for (( i=0; i<n; i++ )); do
      out+="${set:$(( RANDOM % len )):1}"
    done
    printf '%s' "$out"
  }
  UP='ABCDEFGHIJKLMNOPQRSTUVWXYZ'; LO='abcdefghijklmnopqrstuvwxyz'
  DIG='0123456789'; SYM='!@#$%^&*-_=+'
  ALL="$UP$LO$DIG$SYM"
  # 4 guaranteed (one per class) + 20 mixed = 24 chars.
  PERSONA_PASSWORD="$(gen_class "$UP" 1)$(gen_class "$LO" 1)$(gen_class "$DIG" 1)$(gen_class "$SYM" 1)$(gen_class "$ALL" 20)"
  echo "  (generated a random password — printed at the end)"
fi

# Validate (generated OR caller-supplied) against the Cognito policy.
# LC_ALL=C is REQUIRED: under a UTF-8 locale the glob range [A-Z] collates as
# AaBbCc... and matches lowercase too, so the class checks silently pass. Run them
# in a C-locale subshell where [A-Z]/[a-z] are strict ASCII ranges.
pw_problem="$(LC_ALL=C bash -c '
  p="$1"; prob=""
  [ ${#p} -ge 12 ] || prob="must be at least 12 characters"
  case "$p" in *[A-Z]*) ;; *) prob="must have an uppercase character";; esac
  case "$p" in *[a-z]*) ;; *) prob="must have a lowercase character";; esac
  case "$p" in *[0-9]*) ;; *) prob="must have a digit";; esac
  case "$p" in *[!A-Za-z0-9]*) ;; *) prob="must have a symbol";; esac
  printf "%s" "$prob"
' _ "$PERSONA_PASSWORD")"
if [ -n "$pw_problem" ]; then
  echo "ERROR: PERSONA_PASSWORD $pw_problem." >&2
  echo "       PERSONA_PASSWORD is set in your environment to a value that fails the" >&2
  echo "       Cognito policy (>=12 chars, upper+lower+digit+symbol). Either fix it or run" >&2
  echo "       'unset PERSONA_PASSWORD' to let this script generate a valid one." >&2
  echo "       Nothing was created." >&2
  exit 1
fi

echo "== 1. Create registry =="
# Inbound authorization for CONSUMERS (the discovery/data plane + the registry's MCP
# endpoint). Control-plane calls always use IAM regardless of this setting.
#
#   CUSTOM_JWT (default) - consumers present a Cognito bearer token. The authorizer
#     trusts THIS sample's user pool, so the same token the SPA holds is what an
#     external MCP client (Kiro, Amazon Quick, Claude) sends. This is what makes the
#     registry consumable outside AWS credentials.
#   AWS_IAM - consumers sign with SigV4 using their persona-scoped role.
#
# WARNING: authorizerType and the JWT discoveryUrl are IMMUTABLE after creation.
# Switching modes means creating a new registry. Only the allowed clients /
# audiences / scopes / custom claims can be changed later via update-registry.
REGISTRY_AUTH_MODE="${REGISTRY_AUTH_MODE:-CUSTOM_JWT}"
case "$REGISTRY_AUTH_MODE" in
  CUSTOM_JWT|AWS_IAM) ;;
  *) echo "ERROR: REGISTRY_AUTH_MODE must be CUSTOM_JWT or AWS_IAM (got '$REGISTRY_AUTH_MODE')." >&2; exit 1 ;;
esac

if [ "$REGISTRY_AUTH_MODE" = "CUSTOM_JWT" ]; then
  : "${USER_POOL_ID:?USER_POOL_ID missing from state — deploy the Cognito foundation first}"
  : "${USER_POOL_CLIENT_ID:?USER_POOL_CLIENT_ID missing from state — deploy the Cognito foundation first}"
  DISCOVERY_URL="https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"
  # allowedClients matches the token's `client_id` claim, which is what a Cognito
  # ACCESS token carries. (allowedAudience matches `aud`, which is an ID-token claim.)
  DISCOVERY_CONFIG=$(cat <<JSON
{"authorizerType":"CUSTOM_JWT","authorizerConfiguration":{"customJWTAuthorizer":{"discoveryUrl":"${DISCOVERY_URL}","allowedClients":["${USER_POOL_CLIENT_ID}"]}}}
JSON
)
  echo "  authorizer : CUSTOM_JWT (Cognito user pool $USER_POOL_ID)"
  echo "  discoveryUrl: $DISCOVERY_URL"
  echo "  allowedClients: [$USER_POOL_CLIENT_ID]"
else
  DISCOVERY_CONFIG='{"authorizerType":"AWS_IAM"}'
  echo "  authorizer : AWS_IAM (SigV4 discovery)"
fi

# Registry name: override with REGISTRY_NAME to run a second, isolated copy.
REGISTRY_NAME="${REGISTRY_NAME:-AgentRegistryDemo}"
echo "  name       : $REGISTRY_NAME"
REG_ARN=$(aws agent-registry-control create-registry \
  --name "$REGISTRY_NAME" \
  --description "Demo registry for the Agent Registry UI" \
  --discovery-configuration "$DISCOVERY_CONFIG" \
  --approval-configuration '{"autoApprovalRules":[]}' \
  --tags Sample=agent-registry-ui,ManagedBy=deploy-scripts \
  --query registryArn --output text) || { echo "create-registry failed"; exit 1; }
REG_ID="${REG_ARN##*/}"
echo "REGISTRY_ARN=$REG_ARN"
echo "REGISTRY_ID=$REG_ID"
# Record in the state file, REPLACING any previous registry ids (a re-run must not
# accumulate stale REGISTRY_* lines - teardown sources this file).
if [ -f "$ST" ]; then
  grep -vE '^REGISTRY_(ARN|ID|AUTH_MODE)=' "$ST" > "$ST.tmp" 2>/dev/null || true
  mv "$ST.tmp" "$ST"
fi
{
  echo "REGISTRY_ARN=$REG_ARN"
  echo "REGISTRY_ID=$REG_ID"
  echo "REGISTRY_AUTH_MODE=$REGISTRY_AUTH_MODE"
} >> "$ST"

echo "== 2. Wait for registry READY =="
for i in $(seq 1 30); do
  S=$(aws agent-registry-control get-registry --registry-id "$REG_ID" --query status --output text 2>/dev/null || true)
  echo "  status=$S"
  [ "$S" = "READY" ] && break
  sleep 5
done

echo "== 3. Seed records (all types, full lifecycle) =="
mk_record () {  # name type descriptors-json [version] [tags]  -> echoes recordId
  # CreateRegistryRecord takes tags directly, so a seeded record is tagged in one
  # call. (UpdateRegistryRecord has no tags field — later changes go through
  # tag-resource / untag-resource against the record ARN.)
  local arn tags_arg=()
  [ -n "${5:-}" ] && tags_arg=(--tags "$5")
  arn=$(aws agent-registry-control create-registry-record \
    --registry-id "$REG_ID" --name "$1" --record-type "$2" \
    --descriptors "$3" --record-version "${4:-1.0.0}" \
    "${tags_arg[@]}" \
    --query recordArn --output text) || true
  echo "${arn##*/}"
}
approve () {  # recordId  (submit + approve; registry uses manual approval)
  aws agent-registry-control submit-registry-record-for-approval --registry-id "$REG_ID" --record-id "$1" >/dev/null 2>&1 || true
  sleep 1
  aws agent-registry-control update-registry-record-status --registry-id "$REG_ID" --record-id "$1" \
    --status APPROVED --status-reason "Seeded + approved for demo" >/dev/null 2>&1 || true
}
submit_only () {  # recordId  (leave PENDING_APPROVAL)
  aws agent-registry-control submit-registry-record-for-approval --registry-id "$REG_ID" --record-id "$1" >/dev/null 2>&1 || true
}

# --- MCP (server.json, schema 2025-12-11) ---
# The 5th argument tags the record at creation.
MCP1=$(mk_record "incident-mcp-server" MCP '{"mcpServer":{"data":"{\"name\":\"my-org/incident-mcp\",\"description\":\"Incident response MCP server\",\"version\":\"1.0.0\"}","dataSchemaVersion":"2025-12-11"}}' 1.0.0 "owner=sre-team,env=prod,tier=critical")
MCP2=$(mk_record "deploy-mcp-server" MCP '{"mcpServer":{"data":"{\"name\":\"my-org/deploy-mcp\",\"description\":\"Deployment automation MCP server\",\"version\":\"2.1.0\"}","dataSchemaVersion":"2025-12-11"}}' 2.1.0 "owner=platform-team,env=prod,tier=standard")
MCP3=$(mk_record "observability-mcp-server" MCP '{"mcpServer":{"data":"{\"name\":\"my-org/observability-mcp\",\"description\":\"Metrics and logs query MCP server\",\"version\":\"0.9.0\"}","dataSchemaVersion":"2025-12-11"}}' 0.9.0 "owner=observability-team,env=dev")

# --- AGENT (A2A card, schema 0.3) ---
AGENT1=$(mk_record "triage-agent" AGENT '{"a2aAgentCard":{"data":"{\"name\":\"Triage Agent\",\"description\":\"Triages incidents\",\"version\":\"1.0.0\",\"protocolVersion\":\"0.3.0\",\"url\":\"https://api.example.com/a2a\",\"capabilities\":{},\"defaultInputModes\":[\"text/plain\"],\"defaultOutputModes\":[\"text/plain\"],\"skills\":[{\"id\":\"triage\",\"name\":\"Triage\",\"description\":\"Triage an incident\",\"tags\":[\"ops\"]}]}","dataSchemaVersion":"0.3"}}' 1.0.0 "owner=sre-team,env=prod,costCenter=1234")
AGENT2=$(mk_record "rollout-agent" AGENT '{"a2aAgentCard":{"data":"{\"name\":\"Rollout Agent\",\"description\":\"Coordinates progressive rollouts\",\"version\":\"1.2.0\",\"protocolVersion\":\"0.3.0\",\"url\":\"https://api.example.com/rollout/a2a\",\"capabilities\":{},\"defaultInputModes\":[\"text/plain\"],\"defaultOutputModes\":[\"text/plain\"],\"skills\":[{\"id\":\"rollout\",\"name\":\"Rollout\",\"description\":\"Progressive delivery\",\"tags\":[\"deploy\"]}]}","dataSchemaVersion":"0.3"}}' 1.2.0 "owner=platform-team,env=staging")

# --- SKILL (skill definition, schema 0.1.0) ---
SKILL1=$(mk_record "runbook-skill" SKILL '{"agentSkillsDefinition":{"data":"{\"websiteUrl\":\"https://example.com/runbook-skill\",\"repository\":{\"url\":\"https://github.com/example/runbook-skill\",\"source\":\"github\"}}","dataSchemaVersion":"0.1.0"}}' 1.0.0 "owner=sre-team,env=dev")
SKILL2=$(mk_record "pii-redaction-skill" SKILL '{"agentSkillsDefinition":{"data":"{\"websiteUrl\":\"https://example.com/pii-redaction-skill\",\"repository\":{\"url\":\"https://github.com/example/pii-redaction-skill\",\"source\":\"github\"}}","dataSchemaVersion":"0.1.0"}}' 1.0.0 "owner=security-team,env=prod,dataClass=sensitive")

# --- CUSTOM (any JSON, no schema version) ---
CUSTOM1=$(mk_record "incident-enricher-tool" CUSTOM '{"custom":{"data":"{\"kind\":\"lambda-tool\",\"functionArn\":\"arn:aws:lambda:us-east-1:000000000000:function:incident-enricher\",\"description\":\"Enriches incidents with CMDB data\",\"invocation\":\"sync\"}"}}' 1.0.0 "owner=sre-team,env=prod")
CUSTOM2=$(mk_record "oncall-knowledge-base" CUSTOM '{"custom":{"data":"{\"kind\":\"knowledge-base\",\"engine\":\"bedrock-kb\",\"id\":\"KB-2210\",\"description\":\"Runbook knowledge base for on-call\"}"}}' 1.0.0 "owner=sre-team,env=staging")

echo "  created 9 records; driving lifecycle..."
# APPROVED (discoverable): one+ of each type
approve "$MCP1"; approve "$MCP2"; approve "$AGENT1"; approve "$SKILL2"; approve "$CUSTOM1"
# PENDING_APPROVAL: awaiting an approver
submit_only "$AGENT2"; submit_only "$CUSTOM2"
# DRAFT (left as-is): MCP3 (observability-mcp-server), SKILL1 (runbook-skill)
: "$MCP3" "$SKILL1"  # created above; intentionally not advanced past DRAFT
echo "  APPROVED: incident-mcp-server, deploy-mcp-server, triage-agent, pii-redaction-skill, incident-enricher-tool"
echo "  PENDING_APPROVAL: rollout-agent, oncall-knowledge-base"
echo "  DRAFT: observability-mcp-server, runbook-skill"

echo "== 4. Seed one user per persona =="
# The password was resolved + validated at the top of this script, before any AWS
# resource was created (see the PERSONA_PASSWORD block there).

seed_user () {  # email group
  aws cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" \
    --username "$1" --message-action SUPPRESS \
    --user-attributes Name=email,Value="$1" Name=email_verified,Value=true >/dev/null 2>&1 || true
  # Fail LOUDLY if the password can't be set (e.g. policy violation) — do not swallow it.
  if ! aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" \
       --username "$1" --password "$PERSONA_PASSWORD" --permanent 2>/tmp/setpw.err; then
    echo "  ERROR setting password for $1:" >&2
    cat /tmp/setpw.err >&2
    exit 1
  fi
  aws cognito-idp admin-add-user-to-group --user-pool-id "$USER_POOL_ID" \
    --username "$1" --group-name "$2" >/dev/null 2>&1 || true
  echo "  $1 -> $2 (CONFIRMED)"
}
seed_user approver@example.com  AgentRegistryApprover
seed_user publisher@example.com AgentRegistryPublisher
seed_user consumer@example.com  AgentRegistryConsumer

# Persist the password to a 0600 file (gitignored) and print it once.
PW_FILE="$D/state/persona-password.txt"
umask 077
printf '%s\n' "$PERSONA_PASSWORD" > "$PW_FILE"
echo ""
echo "== Persona login password (all 3 users) =="
echo "  $PERSONA_PASSWORD"
echo "  (also saved to $PW_FILE, chmod 600)"

echo ""
echo "== Done. Registry '$REGISTRY_NAME'. Set these in frontend/.env =="
echo "  VITE_REGISTRY_ID=$REG_ID"
echo "  VITE_REGISTRY_AUTH_MODE=$REGISTRY_AUTH_MODE"
if [ "$REGISTRY_AUTH_MODE" = "CUSTOM_JWT" ]; then
  echo ""
  echo "  Registry MCP endpoint (for Kiro / Amazon Quick / Claude):"
  echo "    https://agent-registry.${AWS_REGION}.api.aws/registry/${REG_ID}/mcp"
  echo "  Mint a bearer token with:"
  echo "    aws cognito-idp initiate-auth --region $AWS_REGION \\"
  echo "      --client-id $USER_POOL_CLIENT_ID --auth-flow USER_PASSWORD_AUTH \\"
  echo "      --auth-parameters USERNAME=consumer@example.com,PASSWORD='<password>' \\"
  echo "      --query 'AuthenticationResult.AccessToken' --output text"
fi
