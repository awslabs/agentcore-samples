#!/bin/bash
# Check CloudWatch logs for the agent runtime

RUNTIME_ID="lakehouse_agent-Hhb3lX6y7M"
REGION="us-east-1"

echo "🔍 Checking CloudWatch logs for runtime: $RUNTIME_ID"
echo ""

# Try different log group patterns
LOG_GROUPS=(
    "/aws/bedrock-agentcore/runtime/$RUNTIME_ID"
    "/aws/bedrock/agentcore/runtime/$RUNTIME_ID"
    "/aws/bedrock-agentcore/$RUNTIME_ID"
)

for LOG_GROUP in "${LOG_GROUPS[@]}"; do
    echo "Checking log group: $LOG_GROUP"
    aws logs describe-log-streams \
        --log-group-name "$LOG_GROUP" \
        --region "$REGION" \
        --max-items 5 \
        2>&1 | head -20
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Found log group: $LOG_GROUP"
        echo ""
        echo "📋 Recent logs:"
        aws logs tail "$LOG_GROUP" \
            --region "$REGION" \
            --since 1h \
            --format short \
            2>&1 | head -50
        break
    fi
    echo ""
done

echo ""
echo "🔍 Searching for any bedrock-agentcore log groups..."
aws logs describe-log-groups \
    --region "$REGION" \
    --log-group-name-prefix "/aws/bedrock" \
    2>&1 | grep -i "logGroupName" | head -20
