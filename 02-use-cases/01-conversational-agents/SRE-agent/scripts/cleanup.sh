#!/bin/bash

# Cleanup Script for SRE Agent
# Deletes AgentCore Gateway, Gateway Targets, and Agent Runtime

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default values - can be overridden with environment variables or arguments
DEFAULT_GATEWAY_NAME="sre-agent-gateway"
DEFAULT_RUNTIME_NAME="sre-agent"
DEFAULT_REGION="us-east-1"

# Parse command line arguments
GATEWAY_NAME="${GATEWAY_NAME:-$DEFAULT_GATEWAY_NAME}"
RUNTIME_NAME="${RUNTIME_NAME:-$DEFAULT_RUNTIME_NAME}"
REGION="${REGION:-$DEFAULT_REGION}"
FORCE_DELETE=false

# Function to read gateway name from config.yaml
read_gateway_name_from_config() {
    local config_file="$PROJECT_ROOT/gateway/config.yaml"
    
    if [ -f "$config_file" ]; then
        # Extract gateway_name from YAML, handling quoted and unquoted values
        local gateway_name=$(grep "^gateway_name:" "$config_file" | cut -d':' -f2- | sed 's/^[ \t]*//' | sed 's/^"\([^"]*\)".*/\1/' | sed 's/[ \t]*#.*//')
        if [ -n "$gateway_name" ]; then
            echo "$gateway_name"
            return 0
        fi
    fi
    
    # Return empty string if not found
    echo ""
    return 1
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --gateway-name NAME     Gateway name to delete (default: auto-detect from gateway/config.yaml)"
    echo "  --runtime-name NAME     Runtime name to delete (default: $DEFAULT_RUNTIME_NAME)"
    echo "  --region REGION         AWS region (default: $DEFAULT_REGION)"
    echo "  --force                 Skip confirmation prompts"
    echo "  --help, -h              Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  GATEWAY_NAME           Override default gateway name"
    echo "  RUNTIME_NAME           Override default runtime name"
    echo "  REGION                 Override default AWS region"
    echo ""
    echo "Description:"
    echo "  This script performs complete cleanup of SRE Agent AWS resources:"
    echo "  1. Stops backend servers"
    echo "  2. Deletes all gateway targets"
    echo "  3. Deletes the AgentCore Gateway"
    echo "  4. Deletes memory resources"
    echo "  5. Deletes the AgentCore Runtime"
    echo "  6. Removes generated files"
    echo ""
    echo "Examples:"
    echo "  $0                                          # Use defaults"
    echo "  $0 --gateway-name my-gateway --force       # Custom gateway, no prompts"
    echo "  GATEWAY_NAME=test-gw $0                     # Using environment variable"
}

# Function to confirm deletion
confirm_deletion() {
    if [ "$FORCE_DELETE" = true ]; then
        return 0
    fi
    
    echo "⚠️  WARNING: This will permanently delete the following AWS resources:"
    echo "   - Gateway: $GATEWAY_NAME"
    echo "   - Runtime: $RUNTIME_NAME"
    echo "   - Memory resources (if they exist)"
    echo "   - Region: $REGION"
    echo ""
    echo "   This action cannot be undone!"
    echo ""
    read -p "Are you sure you want to continue? (type 'yes' to confirm): " confirmation
    
    if [ "$confirmation" != "yes" ]; then
        echo "❌ Cleanup cancelled by user"
        exit 1
    fi
}

# Function to stop backend servers
stop_backend_servers() {
    echo "🛑 Stopping backend servers..."
    if [ -f "$PROJECT_ROOT/backend/scripts/stop_demo_backend.sh" ]; then
        cd "$PROJECT_ROOT"
        bash backend/scripts/stop_demo_backend.sh || echo "⚠️  Backend stop script failed or servers not running"
    else
        echo "⚠️  Backend stop script not found, continuing..."
    fi
}

# Function to delete gateway and targets
delete_gateway() {
    echo "🗑️  Deleting AgentCore Gateway and targets..."
    
    # Use the gateway deletion functionality from main.py
    cd "$PROJECT_ROOT/gateway"
    
    # Check if gateway exists and delete it
    python3 -c "
import sys
import boto3
from botocore.exceptions import ClientError
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Import the deletion functions from main.py
sys.path.append('.')
from main import _check_gateway_exists, _delete_gateway

try:
    client = boto3.client('bedrock-agentcore-control', region_name='$REGION')
    
    # Check if gateway exists
    gateway_id = _check_gateway_exists(client, '$GATEWAY_NAME')
    
    if gateway_id:
        print(f'🗑️  Deleting gateway: $GATEWAY_NAME (ID: {gateway_id})')
        _delete_gateway(client, gateway_id)
        print('✅ Gateway and all targets deleted successfully')
    else:
        print('ℹ️  Gateway \"$GATEWAY_NAME\" not found, skipping deletion')
        
except ClientError as e:
    print(f'❌ Failed to delete gateway: {e}')
    sys.exit(1)
except Exception as e:
    print(f'❌ Unexpected error deleting gateway: {e}')
    sys.exit(1)
"
}

# Function to delete agent runtime
delete_agent_runtime() {
    echo "🗑️  Deleting AgentCore Runtime..."
    
    # Use the runtime deletion functionality from deploy_agent_runtime.py
    cd "$PROJECT_ROOT/deployment"
    
    python3 -c "
import sys
import boto3
from botocore.exceptions import ClientError
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Import the deletion functions from deploy_agent_runtime.py
sys.path.append('.')
from deploy_agent_runtime import _get_agent_runtime_id_by_name, _delete_agent_runtime

try:
    client = boto3.client('bedrock-agentcore-control', region_name='$REGION')
    
    # Get runtime ID by name
    runtime_id = _get_agent_runtime_id_by_name(client, '$RUNTIME_NAME')
    
    if runtime_id:
        print(f'🗑️  Deleting runtime: $RUNTIME_NAME (ID: {runtime_id})')
        success = _delete_agent_runtime(client, runtime_id)
        if success:
            print('✅ Agent runtime deleted successfully')
        else:
            print('❌ Failed to delete agent runtime')
            sys.exit(1)
    else:
        print('ℹ️  Runtime \"$RUNTIME_NAME\" not found, skipping deletion')
        
except ClientError as e:
    print(f'❌ Failed to delete runtime: {e}')
    sys.exit(1)
except Exception as e:
    print(f'❌ Unexpected error deleting runtime: {e}')
    sys.exit(1)
"
}

# Function to delete memory resources
delete_memory() {
    echo "🗑️  Deleting Memory Resources..."
    
    cd "$PROJECT_ROOT"
    
    # Check if .memory_id file exists
    if [ ! -f ".memory_id" ]; then
        echo "ℹ️  No .memory_id file found, skipping memory deletion"
        return 0
    fi
    
    MEMORY_ID=$(cat .memory_id | tr -d '\n\r' | xargs)
    if [ -z "$MEMORY_ID" ]; then
        echo "⚠️  Memory ID file is empty, skipping memory deletion"
        return 0
    fi
    
    echo "🗑️  Deleting memory resource: $MEMORY_ID"
    
    # Use the memory deletion functionality from manage_memories.py
    python3 -c "
import sys
import logging
from pathlib import Path

# Add project root to path
project_root = Path('.')
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

try:
    from bedrock_agentcore.memory import MemoryClient
    
    memory_id = '$MEMORY_ID'
    
    print(f'🗑️  Deleting memory resource: {memory_id}')
    memory_client = MemoryClient(region_name='$REGION')
    
    result = memory_client.delete_memory_and_wait(
        memory_id=memory_id, max_wait=300, poll_interval=10
    )
    
    print('✅ Memory resource deleted successfully')
    
except ImportError as e:
    print(f'⚠️  Could not import memory client: {e}')
    print('ℹ️  Memory deletion skipped - ensure dependencies are installed')
except Exception as e:
    print(f'❌ Failed to delete memory resource: {e}')
    # Don't exit with error as this shouldn't stop the cleanup process
    print('⚠️  Continuing with cleanup despite memory deletion failure')
"
}

# Function to clean up generated files
cleanup_local_files() {
    echo "🧹 Cleaning up generated files..."
    
    cd "$PROJECT_ROOT"
    
    # Remove gateway files
    if [ -f "gateway/.gateway_uri" ]; then
        rm -f gateway/.gateway_uri
        echo "✅ Removed gateway/.gateway_uri"
    fi
    
    if [ -f "gateway/.access_token" ]; then
        rm -f gateway/.access_token
        echo "✅ Removed gateway/.access_token"
    fi
    
    # Remove agent runtime files
    if [ -f "deployment/.agent_arn" ]; then
        rm -f deployment/.agent_arn
        echo "✅ Removed deployment/.agent_arn"
    fi
    
    # Remove memory ID file
    if [ -f ".memory_id" ]; then
        rm -f .memory_id
        echo "✅ Removed .memory_id"
    fi
    
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --gateway-name)
            GATEWAY_NAME="$2"
            shift 2
            ;;
        --runtime-name)
            RUNTIME_NAME="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --force)
            FORCE_DELETE=true
            shift
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        *)
            echo "❌ Unknown argument: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Try to auto-detect gateway name from config if not explicitly set
if [ "$GATEWAY_NAME" = "$DEFAULT_GATEWAY_NAME" ]; then
    CONFIG_GATEWAY_NAME=$(read_gateway_name_from_config)
    if [ -n "$CONFIG_GATEWAY_NAME" ]; then
        GATEWAY_NAME="$CONFIG_GATEWAY_NAME"
    fi
fi

# Main execution
echo "🧹 SRE Agent Cleanup Script"
echo "=========================="
echo ""
echo "Configuration:"
echo "  Gateway Name: $GATEWAY_NAME"
if [ -n "$CONFIG_GATEWAY_NAME" ] && [ "$GATEWAY_NAME" = "$CONFIG_GATEWAY_NAME" ]; then
    echo "    (auto-detected from gateway/config.yaml)"
fi
echo "  Runtime Name: $RUNTIME_NAME"
echo "  Region: $REGION"
echo ""

# Confirm deletion unless --force is used
confirm_deletion

echo "🚀 Starting cleanup process..."
echo ""

# Step 1: Stop backend servers
stop_backend_servers
echo ""

# Step 2: Delete gateway and targets
delete_gateway
echo ""

# Step 3: Delete memory resources
delete_memory
echo ""

# Step 4: Delete agent runtime
delete_agent_runtime
echo ""

# Step 5: Clean up generated files
cleanup_local_files
echo ""

echo "✅ Cleanup completed successfully!"
echo ""
echo "📋 Summary of actions performed:"
echo "   ✅ Stopped backend servers"
echo "   ✅ Deleted AgentCore Gateway and all targets"
echo "   ✅ Deleted memory resources"
echo "   ✅ Deleted AgentCore Runtime"
echo "   ✅ Removed generated files"
echo ""
echo "🎯 All SRE Agent AWS resources have been removed."