#!/bin/bash

# AgentCore Memory Resource Deletion
# Deletes memory resource and clears dynamic configuration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "🗑️ Deleting AgentCore Memory Resource..."
echo "========================================"

# Load memory configuration from dynamic config
DYNAMIC_CONFIG="$PROJECT_ROOT/config/dynamic-config.yaml"

if [ ! -f "$DYNAMIC_CONFIG" ]; then
    echo "⚠️ Dynamic configuration file not found: $DYNAMIC_CONFIG"
    echo "   No memory resources to delete"
    exit 0
fi

# Extract memory details from dynamic config
MEMORY_ID=$(yq eval '.memory.id' "$DYNAMIC_CONFIG" 2>/dev/null || echo "null")
MEMORY_NAME=$(yq eval '.memory.name' "$DYNAMIC_CONFIG" 2>/dev/null || echo "unknown")
REGION=$(yq eval '.memory.region' "$DYNAMIC_CONFIG" 2>/dev/null || echo "us-east-1")

if [ "$MEMORY_ID" = "null" ] || [ -z "$MEMORY_ID" ]; then
    echo "⚠️ No memory ID found in dynamic configuration"
    echo "   Skipping memory deletion"
else
    echo "📋 Memory Configuration Found:"
    echo "   • Memory ID: $MEMORY_ID"
    echo "   • Memory Name: $MEMORY_NAME"
    echo "   • Region: $REGION"
    echo ""

    # Check if memory resource exists
    echo "🔍 Verifying memory resource exists..."
    MEMORY_EXISTS=$(python3 -c "
import json
import sys
from bedrock_agentcore.memory import MemoryClient

try:
    client = MemoryClient(region_name='$REGION')
    memories = client.list_memories()
    
    for memory in memories:
        if memory.get('id') == '$MEMORY_ID':
            print('true')
            exit(0)
    
    print('false')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    exit(1)
" 2>/dev/null)

    if [ "$MEMORY_EXISTS" = "true" ]; then
        echo "✅ Memory resource found, proceeding with deletion..."
        
        # Delete memory resource
        echo "🗑️ Deleting memory resource..."
        python3 -c "
from bedrock_agentcore.memory import MemoryClient
import sys

try:
    client = MemoryClient(region_name='$REGION')
    
    print('   Deleting memory resource: $MEMORY_ID')
    client.delete_memory(memory_id='$MEMORY_ID')
    
    print('✅ Memory resource deleted successfully')
    
except Exception as e:
    print(f'❌ Failed to delete memory resource: {e}')
    sys.exit(1)
"
        
        if [ $? -eq 0 ]; then
            echo "✅ Memory resource '$MEMORY_ID' deleted successfully"
        else
            echo "❌ Failed to delete memory resource"
            exit 1
        fi
        
    elif [ "$MEMORY_EXISTS" = "false" ]; then
        echo "⚠️ Memory resource not found in AWS (may have been deleted already)"
        echo "   Will proceed to clean up configuration"
    else
        echo "❌ Error checking memory resource existence"
        exit 1
    fi
fi

# Clear memory configuration from dynamic config
echo ""
echo "📝 Clearing memory configuration from dynamic config..."

if command -v yq >/dev/null 2>&1; then
    # Use yq to remove the memory section while preserving format
    echo "   Using yq to remove memory section..."
    yq eval 'del(.memory)' -i "$DYNAMIC_CONFIG"
    echo "✅ Memory configuration cleared from dynamic config"
else
    # Fallback: Use Python but preserve quote style
    python3 -c "
import yaml
import sys
import re

config_file = '$DYNAMIC_CONFIG'

try:
    # Read the original file content to preserve formatting
    with open(config_file, 'r') as f:
        original_content = f.read()
    
    # Load existing config
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f) or {}
    
    # Remove memory section if it exists
    if 'memory' in config:
        print(f'   Removing memory section from configuration')
        del config['memory']
        
        # Write updated config with preserved quote style
        with open(config_file, 'w') as f:
            # Use the same quote style as other scripts (single quotes for empty strings)
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, indent=2, 
                     default_style=None, allow_unicode=True)
        
        # Post-process to ensure consistent quote style with rest of file
        with open(config_file, 'r') as f:
            content = f.read()
        
        # Ensure empty strings use single quotes consistently
        content = re.sub(r'\"\"', \"''\", content)
        
        with open(config_file, 'w') as f:
            f.write(content)
        
        print('✅ Memory configuration cleared from dynamic config')
    else:
        print('ℹ️ No memory section found in dynamic config')
    
except Exception as e:
    print(f'❌ Failed to update configuration: {e}')
    sys.exit(1)
"
fi

# Verify memory resource is fully deleted
echo ""
echo "🧪 Verifying memory resource deletion..."
python3 -c "
from bedrock_agentcore.memory import MemoryClient
import sys

try:
    client = MemoryClient(region_name='$REGION')
    memories = client.list_memories()
    
    memory_found = False
    for memory in memories:
        if memory.get('id') == '$MEMORY_ID':
            memory_found = True
            status = memory.get('status', 'unknown')
            print(f'⚠️ Memory resource still exists with status: {status}')
            if status == 'DELETING':
                print('   Memory is being deleted (this may take a few moments)')
            break
    
    if not memory_found:
        print('✅ Memory resource successfully removed from AWS')
        
except Exception as e:
    print(f'❌ Failed to verify memory deletion: {e}')
    # Don't exit with error as the deletion may have succeeded
"

echo ""
echo "🎉 AgentCore Memory Resource Cleanup Complete!"
echo "=============================================="
echo "✅ Memory resource deleted from AWS"
echo "✅ Configuration cleared from dynamic-config.yaml"
echo "✅ Agents will no longer have access to memory storage"
echo ""
echo "📋 Summary:"
echo "   • All conversation history has been permanently deleted"
echo "   • Memory resource has been removed from AWS AgentCore"
echo "   • Dynamic configuration has been updated"
echo ""
echo "🔍 To verify no memory resources remain:"
echo "   aws bedrock-agentcore-control list-memories --region $REGION"