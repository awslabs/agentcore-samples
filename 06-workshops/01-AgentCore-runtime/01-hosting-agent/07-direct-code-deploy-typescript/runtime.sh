#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${BUCKET:-bedrock-agentcore-code-${ACCOUNT_ID}-${REGION}}"
PREFIX="typescript_deploy/deployment_package.zip"
AGENT_NAME="my_typescript_agent"

create_runtime() {
  local role_arn="${ROLE_ARN:?Set ROLE_ARN before calling create}"

  aws bedrock-agentcore-control create-agent-runtime \
    --region "$REGION" \
    --agent-runtime-name "$AGENT_NAME" \
    --role-arn "$role_arn" \
    --agent-runtime-artifact '{
      "codeConfiguration": {
        "code": {
          "s3": {
            "bucket": "'"$BUCKET"'",
            "prefix": "'"$PREFIX"'"
          }
        },
        "runtime": "NODE_22",
        "entryPoint": ["app.js"]
      }
    }' \
    --network-configuration '{"networkMode": "PUBLIC"}' \
    --protocol-configuration '{"serverProtocol": "HTTP"}' \
    --query '{agentRuntimeArn: agentRuntimeArn, agentRuntimeId: agentRuntimeId, status: status}' \
    --output json
}

get_runtime() {
  local runtime_id="${1:?Usage: runtime.sh get <agentRuntimeId>}"

  aws bedrock-agentcore-control get-agent-runtime \
    --region "$REGION" \
    --agent-runtime-id "$runtime_id" \
    --query '{agentRuntimeArn: agentRuntimeArn, agentRuntimeId: agentRuntimeId, agentRuntimeName: agentRuntimeName, status: status, createdAt: createdAt, lastUpdatedAt: lastUpdatedAt}' \
    --output json
}

wait_ready() {
  local runtime_id="${1:?Usage: runtime.sh wait <agentRuntimeId>}"
  local timeout="${2:-300}"
  local elapsed=0

  echo "Waiting for $runtime_id to reach READY..."
  while [ "$elapsed" -lt "$timeout" ]; do
    local status
    status=$(aws bedrock-agentcore-control get-agent-runtime \
      --region "$REGION" \
      --agent-runtime-id "$runtime_id" \
      --query 'status' \
      --output text)
    echo "  status: $status"

    if [ "$status" = "READY" ]; then
      echo "Done."
      return 0
    fi

    case "$status" in
      *_FAILED)
        echo "Failed!"
        aws bedrock-agentcore-control get-agent-runtime \
          --region "$REGION" \
          --agent-runtime-id "$runtime_id" \
          --query 'failureReason' \
          --output text
        exit 1
        ;;
    esac

    sleep 10
    elapsed=$((elapsed + 10))
  done

  echo "Timed out after ${timeout}s"
  exit 1
}

list_runtimes() {
  aws bedrock-agentcore-control list-agent-runtimes \
    --region "$REGION" \
    --query 'agentRuntimes[].{Id: agentRuntimeId, Status: status, Name: agentRuntimeName}' \
    --output table
}

invoke_runtime() {
  local runtime_id="${1:?Usage: runtime.sh invoke <agentRuntimeId> [prompt]}"
  local prompt="${2:-hello, what can you do?}"
  local tmpfile
  tmpfile=$(mktemp)

  aws bedrock-agentcore invoke-agent-runtime \
    --region "$REGION" \
    --agent-runtime-arn "$runtime_id" \
    --content-type "application/json" \
    --accept "application/json" \
    --payload "$(echo -n "{\"prompt\": \"$prompt\"}" | base64)" \
    "$tmpfile"

  cat "$tmpfile"
  echo ""
  rm -f "$tmpfile"
}

update_runtime() {
  local runtime_id="${1:?Usage: runtime.sh update <agentRuntimeId>}"
  local role_arn="${ROLE_ARN:?Set ROLE_ARN before calling update}"

  aws bedrock-agentcore-control update-agent-runtime \
    --region "$REGION" \
    --agent-runtime-id "$runtime_id" \
    --role-arn "$role_arn" \
    --agent-runtime-artifact '{
      "codeConfiguration": {
        "code": {
          "s3": {
            "bucket": "'"$BUCKET"'",
            "prefix": "'"$PREFIX"'"
          }
        },
        "runtime": "NODE_22",
        "entryPoint": ["app.js"]
      }
    }' \
    --network-configuration '{"networkMode": "PUBLIC"}' \
    --protocol-configuration '{"serverProtocol": "HTTP"}' \
    --query '{agentRuntimeArn: agentRuntimeArn, agentRuntimeId: agentRuntimeId, status: status}' \
    --output json
}

delete_runtime() {
  local runtime_id="${1:?Usage: runtime.sh delete <agentRuntimeId>}"

  aws bedrock-agentcore-control delete-agent-runtime \
    --region "$REGION" \
    --agent-runtime-id "$runtime_id" \
    --query '{agentRuntimeId: agentRuntimeId, status: status}' \
    --output json
}

USAGE="Usage: ./runtime.sh <command> [args]

Commands:
  create                              Create the agent runtime
  get     <agentRuntimeId>            Get runtime details
  wait    <agentRuntimeId> [timeout]  Wait until runtime is READY (default 300s)
  list                                List all runtimes
  update  <agentRuntimeId>            Update the runtime (redeploy from S3)
  invoke  <agentRuntimeId> [prompt]   Invoke the agent
  delete  <agentRuntimeId>            Delete the runtime"

case "${1:-}" in
  create)  create_runtime ;;
  get)     get_runtime "${2:-}" ;;
  wait)    wait_ready "${2:-}" "${3:-300}" ;;
  list)    list_runtimes ;;
  update)  update_runtime "${2:-}" ;;
  invoke)  invoke_runtime "${2:-}" "${3:-hello, what can you do?}" ;;
  delete)  delete_runtime "${2:-}" ;;
  *)       echo "$USAGE"; exit 1 ;;
esac
