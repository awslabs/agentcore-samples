#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# debug.sh - Read-only diagnostic commands for AgentCore Identity sample app
#
# Usage:
#   ./debug.sh <command> [args]
#
# Configuration:
#   Agent IDs and AWS settings are loaded from .env file (if present) or
#   from environment variables. Required environment variables:
#     COORDINATOR_AGENT_ID   - e.g. coordinator_agent-XXXXXXXXXX
#     PROFILE_AGENT_ID       - e.g. customer_profile_agent-XXXXXXXXXX
#     ACCOUNTS_AGENT_ID      - e.g. accounts_agent-XXXXXXXXXX
#     AWS_REGION             - defaults to ap-southeast-2
#     AWS_PROFILE            - set in env or .env if needed (not hardcoded)
#
# Commands:
#   status              - Show status of all 3 agent runtimes
#   logs <agent> [mins] - Tail CloudWatch logs (agent: coordinator|profile|accounts, default 5 mins)
#   env <agent>         - Show runtime environment variables
#   config <agent>      - Show full runtime configuration
#   deliveries          - Show log delivery sources and deliveries
#   traces [mins]       - Show recent trace spans from aws/spans log group
#   sampling            - Show X-Ray sampling rules
#   ecr                 - Show ECR image tags for all repos
#   identity            - Show current AWS caller identity
#   all                 - Run all status checks

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

REGION="${AWS_REGION:-ap-southeast-2}"

# Agent ID resolution from environment variables
COORDINATOR_ID="${COORDINATOR_AGENT_ID:-}"
PROFILE_ID="${PROFILE_AGENT_ID:-}"
ACCOUNTS_ID="${ACCOUNTS_AGENT_ID:-}"

# Validate that agent IDs are set
validate_agent_ids() {
    local missing=0
    if [ -z "$COORDINATOR_ID" ]; then
        echo "ERROR: COORDINATOR_AGENT_ID is not set" >&2
        missing=1
    fi
    if [ -z "$PROFILE_ID" ]; then
        echo "ERROR: PROFILE_AGENT_ID is not set" >&2
        missing=1
    fi
    if [ -z "$ACCOUNTS_ID" ]; then
        echo "ERROR: ACCOUNTS_AGENT_ID is not set" >&2
        missing=1
    fi
    if [ $missing -eq 1 ]; then
        echo "" >&2
        echo "Set agent IDs in .env file or as environment variables." >&2
        echo "Example .env entries:" >&2
        echo "  COORDINATOR_AGENT_ID=coordinator_agent-XXXXXXXXXX" >&2
        echo "  PROFILE_AGENT_ID=customer_profile_agent-XXXXXXXXXX" >&2
        echo "  ACCOUNTS_AGENT_ID=accounts_agent-XXXXXXXXXX" >&2
        exit 1
    fi
}

# Agent lookups (bash 3.2 compatible - no associative arrays)
get_agent_id() {
  case "$1" in
    coordinator) echo "$COORDINATOR_ID" ;;
    profile)     echo "$PROFILE_ID" ;;
    accounts)    echo "$ACCOUNTS_ID" ;;
    *) echo ""; return 1 ;;
  esac
}

get_log_group() {
  local agent_id
  agent_id=$(get_agent_id "$1") || return 1
  echo "/aws/bedrock-agentcore/runtimes/${agent_id}-DEFAULT"
}

cmd_identity() {
  echo "=== AWS Identity ==="
  aws sts get-caller-identity --region "$REGION" --output table
}

cmd_status() {
  validate_agent_ids
  echo "=== Agent Runtime Status ==="
  for agent in coordinator profile accounts; do
    id=$(get_agent_id "$agent")
    result=$(aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$id" \
      --region "$REGION" \
      --query '{name:agentRuntimeName,id:agentRuntimeId,version:agentRuntimeVersion,status:status}' \
      --output table 2>&1) || result="ERROR: $result"
    echo ""
    echo "[$agent]"
    echo "$result"
  done
}

cmd_env() {
  validate_agent_ids
  local agent="${1:-coordinator}"
  local id
  id=$(get_agent_id "$agent") || { echo "Unknown agent: $agent (use coordinator|profile|accounts)"; exit 1; }

  echo "=== Environment Variables: $agent ($id) ==="
  aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$id" \
    --region "$REGION" \
    --query 'environmentVariables' \
    --output json 2>&1
}

cmd_config() {
  validate_agent_ids
  local agent="${1:-coordinator}"
  local id
  id=$(get_agent_id "$agent") || { echo "Unknown agent: $agent (use coordinator|profile|accounts)"; exit 1; }

  echo "=== Full Config: $agent ($id) ==="
  aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$id" \
    --region "$REGION" \
    --output json 2>&1
}

cmd_logs() {
  validate_agent_ids
  local agent="${1:-coordinator}"
  local mins="${2:-5}"
  local log_group
  log_group=$(get_log_group "$agent") || { echo "Unknown agent: $agent (use coordinator|profile|accounts)"; exit 1; }

  echo "=== Logs: $agent (last ${mins}m) ==="
  aws logs tail "$log_group" \
    --since "${mins}m" \
    --region "$REGION" 2>&1 | tail -80
}

cmd_deliveries() {
  echo "=== Delivery Sources ==="
  aws logs describe-delivery-sources \
    --region "$REGION" \
    --query 'deliverySources[].{name:name,logType:logType,resource:resourceArn}' \
    --output table 2>&1

  echo ""
  echo "=== Active Deliveries ==="
  aws logs describe-deliveries \
    --region "$REGION" \
    --query 'deliveries[].{id:id,source:deliverySourceName,destType:deliveryDestinationType}' \
    --output table 2>&1
}

cmd_traces() {
  local mins="${1:-5}"
  echo "=== Recent Traces (last ${mins}m) ==="
  aws logs tail "aws/spans" \
    --since "${mins}m" \
    --region "$REGION" 2>&1 | tail -50
}

cmd_sampling() {
  echo "=== X-Ray Sampling Rules ==="
  aws xray get-sampling-rules \
    --region "$REGION" \
    --query 'SamplingRuleRecords[].SamplingRule.{name:RuleName,rate:FixedRate,reservoir:ReservoirSize,service:ServiceName}' \
    --output table 2>&1
}

cmd_ecr() {
  echo "=== ECR Images ==="
  for repo in coordinator-agent customer-profile-agent accounts-agent; do
    echo ""
    echo "[$repo]"
    aws ecr describe-images \
      --repository-name "$repo" \
      --region "$REGION" \
      --query 'sort_by(imageDetails,&imagePushedAt)[-3:].{tags:imageTags[0],pushed:imagePushedAt,size:imageSizeInBytes}' \
      --output table 2>&1
  done
}

cmd_all() {
  cmd_identity
  echo ""
  cmd_status
  echo ""
  cmd_ecr
  echo ""
  cmd_sampling
  echo ""
  cmd_deliveries
}

# Dispatch
case "${1:-help}" in
  identity)   cmd_identity ;;
  status)     cmd_status ;;
  env)        cmd_env "${2:-coordinator}" ;;
  config)     cmd_config "${2:-coordinator}" ;;
  logs)       cmd_logs "${2:-coordinator}" "${3:-5}" ;;
  deliveries) cmd_deliveries ;;
  traces)     cmd_traces "${2:-5}" ;;
  sampling)   cmd_sampling ;;
  ecr)        cmd_ecr ;;
  all)        cmd_all ;;
  help|*)
    echo "Usage: ./debug.sh <command> [args]"
    echo ""
    echo "Commands:"
    echo "  status              Show status of all 3 agent runtimes"
    echo "  logs <agent> [mins] Tail CloudWatch logs (coordinator|profile|accounts)"
    echo "  env <agent>         Show runtime environment variables"
    echo "  config <agent>      Show full runtime configuration"
    echo "  deliveries          Show log delivery sources and deliveries"
    echo "  traces [mins]       Show recent trace spans"
    echo "  sampling            Show X-Ray sampling rules"
    echo "  ecr                 Show ECR image tags"
    echo "  identity            Show AWS caller identity"
    echo "  all                 Run all status checks"
    echo ""
    echo "Configuration:"
    echo "  Agent IDs are loaded from .env file or environment variables:"
    echo "    COORDINATOR_AGENT_ID, PROFILE_AGENT_ID, ACCOUNTS_AGENT_ID"
    echo "  AWS_PROFILE and AWS_REGION can also be set in .env or environment."
    ;;
esac
