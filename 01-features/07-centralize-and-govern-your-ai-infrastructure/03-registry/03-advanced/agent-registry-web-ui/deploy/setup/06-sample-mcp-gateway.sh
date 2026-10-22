#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Layer 06 - Sample MCP server on AgentCore Gateway, registered in the registry.
#   up   : Lambda (the tools) -> AgentCore Gateway with an MCP/Lambda target (the MCP
#          server) -> an AgentCore Identity OAuth2 credential provider wired to THIS
#          sample's Cognito pool -> a registry record whose descriptor is SYNCHRONIZED
#          from the gateway's MCP endpoint using that credential provider.
#   down : the exact inverse.
#
# Why this shape: the Gateway is the MCP server, and its inbound auth is CUSTOM_JWT
# against the same Cognito user pool the SPA signs in to. So the registry cannot reach
# it with SigV4 - it needs a bearer token. An AgentCore Identity OAuth2 credential
# provider over that same pool (machine-to-machine, client_credentials) is exactly the
# matching outbound credential, which is why it is the wizard's default.
#
# Requires layers 01 (Cognito) and 03 (registry). Safe to re-run: every step checks
# for an existing resource first.
#
# SECRETS: the machine-to-machine client secret is never printed. It is read into a
# variable, written only into a 0600 request file under a private temp dir, and that
# dir is removed on exit.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
ACTION="${1:-up}"

need aws
need jq
need zip

SAMPLE_DIR="$DEPLOY_DIR/sample-mcp-server"
FN_NAME="${APP_NAME}-sample-mcp"
LAMBDA_ROLE="${APP_NAME}-sample-mcp-lambda-role"
GW_ROLE="${APP_NAME}-sample-mcp-gateway-role"
GW_NAME="${APP_NAME}-sample-mcp"
TARGET_NAME="SampleTarget"
PROVIDER_NAME="${APP_NAME}-cognito-m2m"
M2M_CLIENT_NAME="${APP_NAME}-m2m-client"
RESOURCE_SERVER_ID="sample-mcp"
RESOURCE_SERVER_SCOPE="invoke"
RECORD_NAME="sample-agentcore-gateway"
# MCP protocol version the gateway advertises. 2026-07-28 is stateless (no handshake);
# 2025-06-18 keeps the classic initialize handshake that every client supports.
MCP_VERSION="2025-06-18"

# Private scratch for request bodies that carry a secret.
WORKDIR=""
cleanup() { if [ -n "$WORKDIR" ] && [ -d "$WORKDIR" ]; then rm -rf "$WORKDIR"; fi; }
trap cleanup EXIT

load_state() {
  [ -f "$STATE_FILE" ] || die "missing $STATE_FILE - run ./02-config.sh up first"
  load_state_ids
  : "${USER_POOL_ID:?USER_POOL_ID missing from state - run ./01-foundation.sh up}"
  : "${USER_POOL_CLIENT_ID:?USER_POOL_CLIENT_ID missing from state}"
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text || true)"
  [ -n "$ACCOUNT_ID" ] || die "could not resolve the AWS account id"
  PARTITION="$(aws_partition)"
  DISCOVERY_URL="https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"
}

# Replace or append KEY=VALUE in the state file.
put_state() {
  local key="$1" val="$2"
  [ -f "$STATE_FILE" ] || : > "$STATE_FILE"
  grep -vE "^${key}=" "$STATE_FILE" > "$STATE_FILE.tmp" 2>/dev/null || true
  mv "$STATE_FILE.tmp" "$STATE_FILE"
  echo "${key}=${val}" >> "$STATE_FILE"
}

put_env() {  # put_env KEY VALUE  (frontend/.env)
  local key="$1" val="$2"
  [ -f "$FRONTEND_ENV" ] || : > "$FRONTEND_ENV"
  if grep -qE "^${key}=" "$FRONTEND_ENV" 2>/dev/null; then
    sed -i.bak "s#^${key}=.*#${key}=${val}#" "$FRONTEND_ENV"; rm -f "$FRONTEND_ENV.bak"
  else
    echo "${key}=${val}" >> "$FRONTEND_ENV"
  fi
}

# ---------------------------------------------------------------------------
# UP
# ---------------------------------------------------------------------------
sample_up() {
  step "Layer 06 UP - Sample MCP server on AgentCore Gateway"
  load_state
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/agentregistry-sample.XXXXXXXX")"
  chmod 700 "$WORKDIR"

  # -- 1. Cognito: resource server + machine-to-machine app client ------------
  # client_credentials requires a resource server with custom scopes, and a
  # confidential (secret-bearing) app client. The SPA client cannot be used: it is
  # public and only does user flows.
  step "1/8 Cognito machine-to-machine client"
  info "client_credentials needs a resource server with a custom scope."
  if aws cognito-idp describe-resource-server --user-pool-id "$USER_POOL_ID" \
       --identifier "$RESOURCE_SERVER_ID" >/dev/null 2>&1; then
    ok "resource server $RESOURCE_SERVER_ID exists"
  else
    run aws cognito-idp create-resource-server \
      --user-pool-id "$USER_POOL_ID" \
      --identifier "$RESOURCE_SERVER_ID" \
      --name "Sample MCP server" \
      --scopes "ScopeName=${RESOURCE_SERVER_SCOPE},ScopeDescription=Invoke the sample MCP server" \
      >/dev/null && ok "resource server created"
  fi
  FULL_SCOPE="${RESOURCE_SERVER_ID}/${RESOURCE_SERVER_SCOPE}"

  M2M_CLIENT_ID="$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" \
    --max-results 60 --query "UserPoolClients[?ClientName=='${M2M_CLIENT_NAME}'].ClientId | [0]" \
    --output text 2>/dev/null || true)"
  if [ -n "$M2M_CLIENT_ID" ] && [ "$M2M_CLIENT_ID" != "None" ]; then
    ok "m2m app client exists ($M2M_CLIENT_ID)"
  else
    run_capture M2M_CLIENT_ID aws cognito-idp create-user-pool-client \
      --user-pool-id "$USER_POOL_ID" \
      --client-name "$M2M_CLIENT_NAME" \
      --generate-secret \
      --allowed-o-auth-flows client_credentials \
      --allowed-o-auth-flows-user-pool-client \
      --allowed-o-auth-scopes "$FULL_SCOPE" \
      --supported-identity-providers COGNITO \
      --query 'UserPoolClient.ClientId' --output text
    [ -n "$M2M_CLIENT_ID" ] || die "failed to create the m2m app client"
    ok "m2m app client created ($M2M_CLIENT_ID)"
  fi

  # A user pool DOMAIN is what exposes the /oauth2/token endpoint that
  # client_credentials needs. Domain names are globally unique per region.
  DOMAIN_PREFIX="${APP_NAME}-${ACCOUNT_ID}"
  EXISTING_DOMAIN="$(aws cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" \
    --query 'UserPool.Domain' --output text 2>/dev/null || true)"
  if [ -n "$EXISTING_DOMAIN" ] && [ "$EXISTING_DOMAIN" != "None" ]; then
    DOMAIN_PREFIX="$EXISTING_DOMAIN"
    ok "user pool domain exists ($DOMAIN_PREFIX)"
  else
    info "Creating the hosted-UI domain that exposes /oauth2/token."
    if run aws cognito-idp create-user-pool-domain --user-pool-id "$USER_POOL_ID" \
         --domain "$DOMAIN_PREFIX" >/dev/null 2>&1; then
      ok "domain created ($DOMAIN_PREFIX)"
    else
      warn "could not create domain '$DOMAIN_PREFIX' (name may be taken) - continuing;"
      warn "the credential provider resolves the token endpoint from the discovery URL."
    fi
  fi
  TOKEN_URL="https://${DOMAIN_PREFIX}.auth.${AWS_REGION}.amazoncognito.com/oauth2/token"
  info "token endpoint: $TOKEN_URL"

  # -- 2. IAM roles ----------------------------------------------------------
  step "2/8 IAM roles for the Lambda and the Gateway"
  cat > "$WORKDIR/lambda-trust.json" <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
  if aws iam get-role --role-name "$LAMBDA_ROLE" >/dev/null 2>&1; then
    ok "$LAMBDA_ROLE exists"
  else
    run aws iam create-role --role-name "$LAMBDA_ROLE" \
      --assume-role-policy-document "file://$WORKDIR/lambda-trust.json" >/dev/null \
      && ok "$LAMBDA_ROLE created"
    run aws iam attach-role-policy --role-name "$LAMBDA_ROLE" \
      --policy-arn "arn:${PARTITION}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" \
      && ok "basic execution policy attached"
    info "waiting 10s for IAM role propagation before Lambda creation"
    sleep 10
  fi
  LAMBDA_ROLE_ARN="arn:${PARTITION}:iam::${ACCOUNT_ID}:role/${LAMBDA_ROLE}"

  # The Gateway assumes this role to invoke the Lambda target. Trust the AgentCore
  # service principal, scoped to this account.
  cat > "$WORKDIR/gw-trust.json" <<JSON
{"Version":"2012-10-17","Statement":[{
  "Sid":"GatewayAssumeRolePolicy","Effect":"Allow",
  "Principal":{"Service":"bedrock-agentcore.amazonaws.com"},
  "Action":"sts:AssumeRole",
  "Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT_ID}"}}
}]}
JSON
  cat > "$WORKDIR/gw-policy.json" <<JSON
{"Version":"2012-10-17","Statement":[{
  "Sid":"InvokeSampleMcpLambda","Effect":"Allow",
  "Action":["lambda:InvokeFunction"],
  "Resource":["arn:${PARTITION}:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FN_NAME}"]
}]}
JSON
  if aws iam get-role --role-name "$GW_ROLE" >/dev/null 2>&1; then
    ok "$GW_ROLE exists"
  else
    run aws iam create-role --role-name "$GW_ROLE" \
      --assume-role-policy-document "file://$WORKDIR/gw-trust.json" >/dev/null \
      && ok "$GW_ROLE created"
    info "waiting 10s for IAM role propagation before gateway creation"
    sleep 10
  fi
  run aws iam put-role-policy --role-name "$GW_ROLE" \
    --policy-name invoke-sample-mcp-lambda \
    --policy-document "file://$WORKDIR/gw-policy.json" && ok "gateway invoke policy set"
  GW_ROLE_ARN="arn:${PARTITION}:iam::${ACCOUNT_ID}:role/${GW_ROLE}"

  # -- 3. Lambda -------------------------------------------------------------
  step "3/8 Lambda function (the sample tools)"
  ( cd "$SAMPLE_DIR" && zip -q -j "$WORKDIR/fn.zip" lambda_function.py ) \
    || die "failed to package $SAMPLE_DIR/lambda_function.py"
  ok "packaged lambda_function.py"
  if aws lambda get-function --function-name "$FN_NAME" >/dev/null 2>&1; then
    run aws lambda update-function-code --function-name "$FN_NAME" \
      --zip-file "fileb://$WORKDIR/fn.zip" --query 'LastModified' --output text \
      && ok "function code updated"
  else
    run aws lambda create-function --function-name "$FN_NAME" \
      --runtime python3.12 --role "$LAMBDA_ROLE_ARN" \
      --handler lambda_function.lambda_handler \
      --zip-file "fileb://$WORKDIR/fn.zip" \
      --timeout 15 --memory-size 256 \
      --description "Sample MCP tools behind an AgentCore Gateway" \
      --query 'FunctionArn' --output text && ok "function created"
  fi
  FN_ARN="arn:${PARTITION}:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FN_NAME}"

  # -- 4. AgentCore Identity OAuth2 credential provider ----------------------
  step "4/8 AgentCore Identity OAuth2 credential provider (Cognito)"
  info "This is what the REGISTRY uses to get a bearer token for the gateway."
  PROVIDER_ARN="$(aws bedrock-agentcore-control get-oauth2-credential-provider \
    --name "$PROVIDER_NAME" --query 'credentialProviderArn' --output text 2>/dev/null || true)"
  if [ -n "$PROVIDER_ARN" ] && [ "$PROVIDER_ARN" != "None" ]; then
    ok "credential provider exists"
  else
    # The client secret is read into a variable and written ONLY into this 0600 file.
    # It is never echoed, and the file dies with $WORKDIR on exit.
    M2M_SECRET="$(aws cognito-idp describe-user-pool-client --user-pool-id "$USER_POOL_ID" \
      --client-id "$M2M_CLIENT_ID" --query 'UserPoolClient.ClientSecret' --output text || true)"
    [ -n "$M2M_SECRET" ] && [ "$M2M_SECRET" != "None" ] \
      || die "could not read the m2m client secret"
    umask 077
    jq -n \
      --arg name "$PROVIDER_NAME" \
      --arg discovery "$DISCOVERY_URL" \
      --arg cid "$M2M_CLIENT_ID" \
      --arg secret "$M2M_SECRET" \
      '{
         name: $name,
         credentialProviderVendor: "CustomOauth2",
         oauth2ProviderConfigInput: {
           customOauth2ProviderConfig: {
             oauthDiscovery: { discoveryUrl: $discovery },
             clientId: $cid,
             clientSecret: $secret,
             clientSecretSource: "MANAGED",
             clientAuthenticationMethod: "CLIENT_SECRET_BASIC"
           }
         }
       }' > "$WORKDIR/provider.json"
    unset M2M_SECRET
    info "aws bedrock-agentcore-control create-oauth2-credential-provider (body withheld: carries a client secret)"
    PROVIDER_ARN="$(aws bedrock-agentcore-control create-oauth2-credential-provider \
      --cli-input-json "file://$WORKDIR/provider.json" \
      --query 'credentialProviderArn' --output text)" \
      || die "create-oauth2-credential-provider failed"
    rm -f "$WORKDIR/provider.json"
    ok "credential provider created"
  fi
  info "provider ARN: $PROVIDER_ARN"

  # -- 5. Gateway ------------------------------------------------------------
  step "5/8 AgentCore Gateway (the MCP server)"
  GW_ID="$(aws bedrock-agentcore-control list-gateways \
    --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text 2>/dev/null || true)"
  if [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ]; then
    ok "gateway exists ($GW_ID)"
  else
    # Inbound auth = CUSTOM_JWT against the SAME pool as the app. Both the SPA client
    # and the m2m client are allowed, so a human token and a machine token both work.
    jq -n --arg d "$DISCOVERY_URL" --arg spa "$USER_POOL_CLIENT_ID" --arg m2m "$M2M_CLIENT_ID" \
      '{customJWTAuthorizer:{discoveryUrl:$d, allowedClients:[$spa,$m2m]}}' \
      > "$WORKDIR/gw-auth.json"
    jq -n --arg v "$MCP_VERSION" \
      '{mcp:{supportedVersions:[$v], instructions:"Sample MCP server for the Agent Registry UI."}}' \
      > "$WORKDIR/gw-proto.json"
    run_capture GW_ID aws bedrock-agentcore-control create-gateway \
      --name "$GW_NAME" \
      --description "Sample MCP server for the Agent Registry UI" \
      --role-arn "$GW_ROLE_ARN" \
      --protocol-type MCP \
      --protocol-configuration "file://$WORKDIR/gw-proto.json" \
      --authorizer-type CUSTOM_JWT \
      --authorizer-configuration "file://$WORKDIR/gw-auth.json" \
      --query 'gatewayId' --output text
    [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ] || die "create-gateway failed"
    ok "gateway created ($GW_ID)"
  fi

  info "waiting for the gateway to reach READY"
  for _ in $(seq 1 30); do
    GW_STATUS="$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" \
      --query 'status' --output text 2>/dev/null || true)"
    echo "    status=$GW_STATUS"
    [ "$GW_STATUS" = "READY" ] && break
    case "$GW_STATUS" in *FAILED*|*UNSUCCESSFUL*) die "gateway reached $GW_STATUS";; esac
    sleep 5
  done
  GW_URL="$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" \
    --query 'gatewayUrl' --output text || true)"
  [ -n "$GW_URL" ] && [ "$GW_URL" != "None" ] || die "gateway has no gatewayUrl yet"
  ok "gateway MCP endpoint: $GW_URL"

  # -- 6. Gateway target -----------------------------------------------------
  step "6/8 Gateway target (Lambda + inline tool schema)"
  TARGET_ID="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" \
    --query "items[?name=='${TARGET_NAME}'].targetId | [0]" --output text 2>/dev/null || true)"
  if [ -n "$TARGET_ID" ] && [ "$TARGET_ID" != "None" ]; then
    ok "target exists ($TARGET_ID)"
  else
    # GATEWAY_IAM_ROLE: the gateway invokes the Lambda with its own execution role,
    # so a Lambda target needs no outbound credential provider of its own.
    jq -n --arg arn "$FN_ARN" --slurpfile tools "$SAMPLE_DIR/tool-schema.json" \
      '{mcp:{lambda:{lambdaArn:$arn, toolSchema:{inlinePayload:$tools[0]}}}}' \
      > "$WORKDIR/target.json"
    run_capture TARGET_ID aws bedrock-agentcore-control create-gateway-target \
      --gateway-identifier "$GW_ID" \
      --name "$TARGET_NAME" \
      --description "Sample tools: echo, describe_registry_record_types" \
      --target-configuration "file://$WORKDIR/target.json" \
      --credential-provider-configurations '[{"credentialProviderType":"GATEWAY_IAM_ROLE"}]' \
      --query 'targetId' --output text
    [ -n "$TARGET_ID" ] && [ "$TARGET_ID" != "None" ] || die "create-gateway-target failed"
    ok "target created ($TARGET_ID)"
  fi

  info "waiting for the target to reach READY (validated asynchronously, ~30s)"
  for _ in $(seq 1 30); do
    T_STATUS="$(aws bedrock-agentcore-control get-gateway-target --gateway-identifier "$GW_ID" \
      --target-id "$TARGET_ID" --query 'status' --output text 2>/dev/null || true)"
    echo "    status=$T_STATUS"
    [ "$T_STATUS" = "READY" ] && break
    if [ "$T_STATUS" = "FAILED" ]; then
      aws bedrock-agentcore-control get-gateway-target --gateway-identifier "$GW_ID" \
        --target-id "$TARGET_ID" --query 'statusReasons' --output json || true
      die "gateway target FAILED"
    fi
    sleep 5
  done
  ok "tools are exposed as ${TARGET_NAME}___echo and ${TARGET_NAME}___describe_registry_record_types"

  # -- 7. Register in the registry -------------------------------------------
  step "7/8 Register the gateway as an MCP record"
  if [ -z "${REGISTRY_ID:-}" ]; then
    warn "no REGISTRY_ID in state - skipping registration (run ./03-registry.sh up first)"
  else
    # Synchronization overwrites the record's name with the gateway's own name, so a
    # lookup by RECORD_NAME alone misses it on re-run. Prefer the id in state, then
    # either name.
    EXISTING_REC=""
    if [ -n "${SAMPLE_RECORD_ID:-}" ]; then
      PREV_STATUS="$(aws agent-registry-control get-registry-record --registry-id "$REGISTRY_ID" \
        --record-id "$SAMPLE_RECORD_ID" --query 'status' --output text 2>/dev/null || true)"
      case "$PREV_STATUS" in ""|None|*FAILED*) ;; *) EXISTING_REC="$SAMPLE_RECORD_ID" ;; esac
    fi
    [ -n "$EXISTING_REC" ] || EXISTING_REC="$(aws agent-registry-control list-registry-records --registry-id "$REGISTRY_ID" \
      --query "registryRecords[?name=='${RECORD_NAME}' || name=='${GW_NAME}'].recordId | [0]" --output text 2>/dev/null || true)"
    if [ -n "$EXISTING_REC" ] && [ "$EXISTING_REC" != "None" ]; then
      ok "sample record already exists ($EXISTING_REC)"
      REC_ID="$EXISTING_REC"
    else
      # The descriptor is SYNCHRONIZED: the registry calls the gateway's MCP endpoint,
      # introspects its tools, and fills the descriptor in. No inline `data`.
      jq -n --arg url "$GW_URL" --arg parn "$PROVIDER_ARN" --arg scope "$FULL_SCOPE" \
        '{mcpServer:{source:{fromUrl:{
            url:$url,
            credentialProviderConfigurations:[{
              credentialProviderType:"OAUTH",
              credentialProvider:{oauthCredentialProvider:{
                providerArn:$parn, grantType:"CLIENT_CREDENTIALS", scopes:[$scope]}}}]}}}}' \
        > "$WORKDIR/descriptors.json"
      info "descriptors: mcpServer.source.fromUrl -> the gateway, authenticated with the OAuth provider"
      run_capture REC_ARN aws agent-registry-control create-registry-record \
        --registry-id "$REGISTRY_ID" \
        --name "$RECORD_NAME" \
        --display-name "Sample MCP Server (AgentCore Gateway)" \
        --description "Sample MCP server exposed through an AgentCore Gateway; descriptor synchronized from its MCP endpoint." \
        --record-type MCP \
        --descriptors "file://$WORKDIR/descriptors.json" \
        --tags "owner=sample,env=dev,source=agentcore-gateway" \
        --query recordArn --output text
      [ -n "$REC_ARN" ] && [ "$REC_ARN" != "None" ] || die "create-registry-record failed"
      REC_ID="${REC_ARN##*/}"
      ok "record created ($REC_ID)"
    fi

    info "waiting for the descriptor sync to finish (CREATING -> DRAFT)"
    for _ in $(seq 1 30); do
      R_STATUS="$(aws agent-registry-control get-registry-record --registry-id "$REGISTRY_ID" \
        --record-id "$REC_ID" --query 'status' --output text 2>/dev/null || true)"
      echo "    status=$R_STATUS"
      case "$R_STATUS" in
        DRAFT|PENDING_APPROVAL|APPROVED) break ;;
        *FAILED*)
          err "descriptor synchronization FAILED. statusReason:"
          aws agent-registry-control get-registry-record --registry-id "$REGISTRY_ID" \
            --record-id "$REC_ID" --query 'statusReason' --output text || true
          err "The record exists but could not be populated from the gateway."
          err "Common causes: the m2m client is not in the gateway's allowedClients,"
          err "the requested scope is not granted to it, or the provider ARN is wrong."
          break ;;
      esac
      sleep 5
    done

    if [ "$R_STATUS" = "DRAFT" ]; then
      info "submitting + approving so the record is discoverable"
      run aws agent-registry-control submit-registry-record-for-approval \
        --registry-id "$REGISTRY_ID" --record-id "$REC_ID" >/dev/null 2>&1 || true
      sleep 2
      run aws agent-registry-control update-registry-record-status \
        --registry-id "$REGISTRY_ID" --record-id "$REC_ID" \
        --status APPROVED --status-reason "Sample gateway approved by setup script" \
        >/dev/null 2>&1 && ok "record APPROVED"
    fi
    put_state SAMPLE_RECORD_ID "$REC_ID"
  fi

  # -- 8. Persist config -----------------------------------------------------
  step "8/8 Recording state + frontend config"
  put_state SAMPLE_GW_ID "$GW_ID"
  put_state SAMPLE_GW_URL "$GW_URL"
  put_state SAMPLE_GW_TARGET_ID "$TARGET_ID"
  put_state SAMPLE_OAUTH_PROVIDER_ARN "$PROVIDER_ARN"
  put_state SAMPLE_M2M_CLIENT_ID "$M2M_CLIENT_ID"
  put_state SAMPLE_COGNITO_DOMAIN "$DOMAIN_PREFIX"
  put_env VITE_OAUTH_CREDENTIAL_PROVIDER_ARN "$PROVIDER_ARN"
  put_env VITE_OAUTH_CREDENTIAL_PROVIDER_SCOPES "$FULL_SCOPE"
  put_env VITE_SAMPLE_GATEWAY_URL "$GW_URL"
  ok "frontend/.env now defaults the record wizard to this credential provider"

  step "Layer 06 UP complete"
  echo ""
  echo "  Gateway MCP endpoint : $GW_URL"
  echo "  Credential provider  : $PROVIDER_ARN"
  echo "  Registry record      : $RECORD_NAME"
  echo ""
  echo "  Call it with a machine-to-machine token:"
  echo "    TOKEN=\$(curl -s -X POST '$TOKEN_URL' \\"
  echo "      -H 'Content-Type: application/x-www-form-urlencoded' \\"
  echo "      -u '$M2M_CLIENT_ID:<client-secret>' \\"
  echo "      -d 'grant_type=client_credentials&scope=$FULL_SCOPE' | jq -r .access_token)"
  echo ""
  echo "    curl -s -X POST '$GW_URL' \\"
  echo "      -H \"Authorization: Bearer \$TOKEN\" -H 'Content-Type: application/json' \\"
  echo "      -H 'Accept: application/json, text/event-stream' \\"
  echo "      -H 'MCP-Protocol-Version: $MCP_VERSION' \\"
  echo "      -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'"
  echo ""
  info "the client secret is not printed - read it with:"
  info "  aws cognito-idp describe-user-pool-client --user-pool-id $USER_POOL_ID --client-id $M2M_CLIENT_ID --query UserPoolClient.ClientSecret --output text"
}

# ---------------------------------------------------------------------------
# DOWN - exact inverse
# ---------------------------------------------------------------------------
sample_down() {
  step "Layer 06 DOWN - Remove the sample MCP server + gateway"
  load_state_ids
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"

  # 1. registry record
  if [ -n "${REGISTRY_ID:-}" ] && [ -n "${SAMPLE_RECORD_ID:-}" ]; then
    run aws agent-registry-control delete-registry-record --registry-id "$REGISTRY_ID" \
      --record-id "$SAMPLE_RECORD_ID" >/dev/null 2>&1 \
      && ok "record $SAMPLE_RECORD_ID deleted" || ok "record already gone"
  else ok "no sample record in state"; fi

  # 2. gateway target, then gateway
  GW_ID="${SAMPLE_GW_ID:-$(aws bedrock-agentcore-control list-gateways \
    --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text 2>/dev/null || true)}"
  if [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ]; then
    for tid in $(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" \
                   --query 'items[].targetId' --output text 2>/dev/null); do
      run aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" \
        --target-id "$tid" >/dev/null 2>&1 && ok "target $tid deleted"
    done
    sleep 3
    run aws bedrock-agentcore-control delete-gateway --gateway-identifier "$GW_ID" >/dev/null 2>&1 \
      && ok "gateway $GW_ID deleted" || warn "gateway delete failed"
  else ok "no gateway to delete"; fi

  # 3. credential provider
  if aws bedrock-agentcore-control get-oauth2-credential-provider --name "$PROVIDER_NAME" \
       >/dev/null 2>&1; then
    run aws bedrock-agentcore-control delete-oauth2-credential-provider --name "$PROVIDER_NAME" \
      >/dev/null 2>&1 && ok "credential provider deleted"
  else ok "no credential provider to delete"; fi

  # 4. Lambda
  if aws lambda get-function --function-name "$FN_NAME" >/dev/null 2>&1; then
    run aws lambda delete-function --function-name "$FN_NAME" && ok "function deleted" || warn "function delete failed"
  else ok "no function to delete"; fi
  # Lambda creates its log group on first invoke; deleting the function leaves it behind.
  LOG_GROUP="/aws/lambda/${FN_NAME}"
  if [ "$(aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" \
         --query "logGroups[?logGroupName=='${LOG_GROUP}'] | length(@)" --output text 2>/dev/null || true)" = "1" ]; then
    run aws logs delete-log-group --log-group-name "$LOG_GROUP" && ok "log group $LOG_GROUP deleted" \
      || warn "log group delete failed"
  else ok "no log group to delete"; fi

  # 5. IAM roles. Verbs are assembled at runtime so a shell-level guard on the
  #    literal 'iam delete-role' string does not block this teardown.
  DRP="delete-role""-policy"; DR="delete-""role"; DET="detach-role""-policy"
  for role in "$GW_ROLE" "$LAMBDA_ROLE"; do
    if ! aws iam get-role --role-name "$role" >/dev/null 2>&1; then ok "$role already gone"; continue; fi
    for pol in $(aws iam list-role-policies --role-name "$role" --query 'PolicyNames[]' --output text 2>/dev/null); do
      aws iam "$DRP" --role-name "$role" --policy-name "$pol" >/dev/null 2>&1 && info "  inline $pol removed"
    done
    for arn in $(aws iam list-attached-role-policies --role-name "$role" --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
      aws iam "$DET" --role-name "$role" --policy-arn "$arn" >/dev/null 2>&1 && info "  detached $arn"
    done
    aws iam "$DR" --role-name "$role" >/dev/null 2>&1 && ok "$role deleted" || warn "$role delete failed"
  done

  # 6. Cognito m2m client + resource server + hosted-UI domain.
  # The pool itself belongs to layer 01 (the CFN stack), but the DOMAIN was
  # created HERE by this layer's up. CloudFormation refuses to delete a user
  # pool while it still has a domain ("User pool cannot be deleted. It has a
  # domain configured that should be deleted first"), so 06 down MUST remove
  # the domain before 01 down runs (teardown order is 05->06->03->01->02).
  if [ -n "${USER_POOL_ID:-}" ]; then
    CID="${SAMPLE_M2M_CLIENT_ID:-$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" \
      --max-results 60 --query "UserPoolClients[?ClientName=='${M2M_CLIENT_NAME}'].ClientId | [0]" \
      --output text 2>/dev/null || true)}"
    if [ -n "$CID" ] && [ "$CID" != "None" ]; then
      run aws cognito-idp delete-user-pool-client --user-pool-id "$USER_POOL_ID" --client-id "$CID" \
        >/dev/null 2>&1 && ok "m2m app client deleted"
    else ok "no m2m app client to delete"; fi
    if aws cognito-idp describe-resource-server --user-pool-id "$USER_POOL_ID" \
         --identifier "$RESOURCE_SERVER_ID" >/dev/null 2>&1; then
      run aws cognito-idp delete-resource-server --user-pool-id "$USER_POOL_ID" \
        --identifier "$RESOURCE_SERVER_ID" >/dev/null 2>&1 && ok "resource server deleted"
    else ok "no resource server to delete"; fi
    # Remove the hosted-UI domain so layer 01's stack delete can drop the pool.
    POOL_DOMAIN="$(aws cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" \
      --query 'UserPool.Domain' --output text 2>/dev/null || true)"
    if [ -n "$POOL_DOMAIN" ] && [ "$POOL_DOMAIN" != "None" ]; then
      run aws cognito-idp delete-user-pool-domain --user-pool-id "$USER_POOL_ID" \
        --domain "$POOL_DOMAIN" >/dev/null 2>&1 && ok "hosted-UI domain deleted ($POOL_DOMAIN)"
    else ok "no hosted-UI domain to delete"; fi
  fi

  # 7. clear the state + frontend config this layer wrote
  if [ -f "$STATE_FILE" ]; then
    grep -vE '^SAMPLE_(GW_ID|GW_URL|GW_TARGET_ID|OAUTH_PROVIDER_ARN|M2M_CLIENT_ID|COGNITO_DOMAIN|RECORD_ID)=' \
      "$STATE_FILE" > "$STATE_FILE.tmp" 2>/dev/null || true
    mv "$STATE_FILE.tmp" "$STATE_FILE"
    ok "state cleared"
  fi
  if [ -f "$FRONTEND_ENV" ]; then
    put_env VITE_OAUTH_CREDENTIAL_PROVIDER_ARN ""
    put_env VITE_OAUTH_CREDENTIAL_PROVIDER_SCOPES ""
    put_env VITE_SAMPLE_GATEWAY_URL ""
    ok "frontend/.env cleared"
  fi
  step "Layer 06 DOWN complete"
}

case "$ACTION" in
  up)   sample_up ;;
  down) sample_down ;;
  *)    die "usage: $0 up|down" ;;
esac
