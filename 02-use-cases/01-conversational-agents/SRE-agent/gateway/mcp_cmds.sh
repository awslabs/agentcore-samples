#!/bin/bash

# MCP Commands Script
# This script contains various MCP commands for testing AgentCore Gateway

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "MCP Gateway Commands"
echo "==================="
echo ""

# Check if required files exist in script directory
if [ ! -f "${SCRIPT_DIR}/.access_token" ]; then
    echo "❌ Error: .access_token file not found in ${SCRIPT_DIR}"
    echo "Run generate_token.py or create_gateway.sh first"
    exit 1
fi

if [ ! -f "${SCRIPT_DIR}/.gateway_uri" ]; then
    echo "❌ Error: .gateway_uri file not found in ${SCRIPT_DIR}"
    echo "Run create_gateway.sh first to create a gateway"
    exit 1
fi

echo "✅ Found .access_token and .gateway_uri files"
echo ""
# List available tools
echo "📋 Listing available tools..."
TOOLS_RESPONSE=$(curl -vvv -sS --request POST --header 'Content-Type: application/json' \
--header "Authorization: Bearer $(cat ${SCRIPT_DIR}/.access_token)" \
--data '{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list"
}' \
"$(cat ${SCRIPT_DIR}/.gateway_uri)/mcp")

echo "$TOOLS_RESPONSE" | jq .

# Parse and display tool summary
echo ""
echo "🔧 Tool Summary:"
echo "================"

# Extract tools array and count
TOOLS_COUNT=$(echo "$TOOLS_RESPONSE" | jq -r '.result.tools | length // 0')
echo "📊 Total Tools Found: $TOOLS_COUNT"

if [ "$TOOLS_COUNT" -gt 0 ]; then
    echo ""
    echo "📝 Tool Names:"
    # Extract and display each tool name
    echo "$TOOLS_RESPONSE" | jq -r '.result.tools[]?.name // empty' | while IFS= read -r tool_name; do
        if [ -n "$tool_name" ]; then
            echo "   • $tool_name"
        fi
    done
else
    echo "❌ No tools found in the response"
fi

echo ""

# Extract and call a specific tool - get_pod_status
echo "🔨 Testing get_pod_status Tool:"
echo "================================"

# Extract the get_pod_status tool name from the tools list
GET_POD_STATUS_TOOL=$(echo "$TOOLS_RESPONSE" | jq -r '.result.tools[]? | select(.name | contains("get_pod_status")) | .name // empty' | head -1)

if [ -n "$GET_POD_STATUS_TOOL" ]; then
    echo "✅ Found tool: $GET_POD_STATUS_TOOL"
    echo ""
    
    # Call get_pod_status with parameters based on OpenAPI spec
    # Parameters: namespace (optional), pod_name (optional)
    echo "📤 Calling $GET_POD_STATUS_TOOL with namespace='production'..."
    
    POD_STATUS_RESPONSE=$(curl -vvv -sS --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer $(cat ${SCRIPT_DIR}/.access_token)" \
    --data '{
      "jsonrpc": "2.0",
      "id": 3,
      "method": "tools/call",
      "params": {
        "name": "'"$GET_POD_STATUS_TOOL"'",
        "arguments": {
          "namespace": "production"
        }
      }
    }' \
    "$(cat ${SCRIPT_DIR}/.gateway_uri)/mcp")
    
    echo "📥 Response:"
    echo "$POD_STATUS_RESPONSE" | jq .
    
    # Try another call with a specific pod name
    echo ""
    echo "📤 Calling $GET_POD_STATUS_TOOL with pod_name='web-app-deployment-5c8d7f9b6d-k2n8p'..."
    
    SPECIFIC_POD_RESPONSE=$(curl -sS --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer $(cat ${SCRIPT_DIR}/.access_token)" \
    --data '{
      "jsonrpc": "2.0",
      "id": 4,
      "method": "tools/call",
      "params": {
        "name": "'"$GET_POD_STATUS_TOOL"'",
        "arguments": {
          "pod_name": "web-app-deployment-5c8d7f9b6d-k2n8p"
        }
      }
    }' \
    "$(cat ${SCRIPT_DIR}/.gateway_uri)/mcp")
    
    echo "📥 Response:"
    echo "$SPECIFIC_POD_RESPONSE" | jq .
else
    echo "❌ get_pod_status tool not found in the tools list"
fi

echo ""

# Extract and call a specific tool - get_performance_metrics
echo "🔨 Testing get_performance_metrics Tool:"
echo "========================================"

# Extract the get_performance_metrics tool name from the tools list
GET_PERFORMANCE_METRICS_TOOL=$(echo "$TOOLS_RESPONSE" | jq -r '.result.tools[]? | select(.name | contains("get_performance_metrics")) | .name // empty' | head -1)

if [ -n "$GET_PERFORMANCE_METRICS_TOOL" ]; then
    echo "✅ Found tool: $GET_PERFORMANCE_METRICS_TOOL"
    echo ""
    
    # Test 1: Call get_performance_metrics with metric_type='response_time' and service
    echo "📤 Calling $GET_PERFORMANCE_METRICS_TOOL with metric_type='response_time' and service='web-service'..."
    
    PERF_METRICS_RESPONSE_1=$(curl -sS --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer $(cat ${SCRIPT_DIR}/.access_token)" \
    --data '{
      "jsonrpc": "2.0",
      "id": 5,
      "method": "tools/call",
      "params": {
        "name": "'"$GET_PERFORMANCE_METRICS_TOOL"'",
        "arguments": {
          "metric_type": "response_time",
          "service": "web-service"
        }
      }
    }' \
    "$(cat ${SCRIPT_DIR}/.gateway_uri)/mcp")
    
    echo "📥 Response:"
    echo "$PERF_METRICS_RESPONSE_1" | jq .
    
    # Test 2: Call get_performance_metrics with metric_type='memory_usage' and time range
    echo ""
    echo "📤 Calling $GET_PERFORMANCE_METRICS_TOOL with metric_type='memory_usage' and time range..."
    
    PERF_METRICS_RESPONSE_2=$(curl -sS --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer $(cat ${SCRIPT_DIR}/.access_token)" \
    --data '{
      "jsonrpc": "2.0",
      "id": 6,
      "method": "tools/call",
      "params": {
        "name": "'"$GET_PERFORMANCE_METRICS_TOOL"'",
        "arguments": {
          "metric_type": "memory_usage",
          "start_time": "2024-01-15T14:00:00Z",
          "end_time": "2024-01-15T15:00:00Z"
        }
      }
    }' \
    "$(cat ${SCRIPT_DIR}/.gateway_uri)/mcp")
    
    echo "📥 Response:"
    echo "$PERF_METRICS_RESPONSE_2" | jq .
    
    # Test 3: Call get_performance_metrics with metric_type='throughput' with service and time range
    echo ""
    echo "📤 Calling $GET_PERFORMANCE_METRICS_TOOL with metric_type='throughput', service='api-service' and time range..."
    
    PERF_METRICS_RESPONSE_3=$(curl -sS --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer $(cat ${SCRIPT_DIR}/.access_token)" \
    --data '{
      "jsonrpc": "2.0",
      "id": 7,
      "method": "tools/call",
      "params": {
        "name": "'"$GET_PERFORMANCE_METRICS_TOOL"'",
        "arguments": {
          "metric_type": "throughput",
          "service": "api-service",
          "start_time": "2024-01-15T13:00:00Z",
          "end_time": "2024-01-15T14:00:00Z"
        }
      }
    }' \
    "$(cat ${SCRIPT_DIR}/.gateway_uri)/mcp")
    
    echo "📥 Response:"
    echo "$PERF_METRICS_RESPONSE_3" | jq .
else
    echo "❌ get_performance_metrics tool not found in the tools list"
fi

echo ""
