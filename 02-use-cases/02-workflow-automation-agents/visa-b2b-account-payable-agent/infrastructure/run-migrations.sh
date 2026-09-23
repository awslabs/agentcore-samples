#!/bin/bash

# Run database migrations and seed data

set -e

# Set AWS region
export AWS_DEFAULT_REGION=us-east-1

echo "🗄️  Running Database Migrations & Seed"
echo "======================================"
echo ""

# Get migration Lambda function name
MIGRATION_LAMBDA=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayLambdaStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`MigrationLambdaName`].OutputValue' \
    --output text)

if [ -z "$MIGRATION_LAMBDA" ]; then
    echo "❌ Migration Lambda not found"
    echo "Make sure Lambda stack is deployed: ./deploy-lambda.sh"
    exit 1
fi

echo "✓ Found migration Lambda: $MIGRATION_LAMBDA"
echo ""

echo "🚀 Invoking migration Lambda..."
echo "This will:"
echo "  1. Create database tables"
echo "  2. Seed test data (users, vendors, purchase orders)"
echo ""

# Invoke the Lambda function
RESULT=$(aws lambda invoke \
    --function-name $MIGRATION_LAMBDA \
    --region us-east-1 \
    --payload '{}' \
    --cli-binary-format raw-in-base64-out \
    response.json)

# Check if invocation was successful
if [ $? -eq 0 ]; then
    echo "✓ Lambda invoked successfully"
    echo ""
    
    # Show the response
    echo "📋 Response:"
    cat response.json | jq .
    echo ""
    
    # Check if migration was successful
    STATUS=$(cat response.json | jq -r '.statusCode')
    if [ "$STATUS" = "200" ]; then
        echo "✅ Migration and seed completed successfully!"
        echo ""
        echo "📊 Test data created:"
        echo "  - 3 users (receiving1, qc1, treasury1)"
        echo "  - 3 vendors (Acme, TechSupply, Global Parts)"
        echo "  - 10 purchase orders with items"
        echo ""
        echo "🔐 Test credentials:"
        echo "  Username: receiving1"
        echo "  Password: password123"
        echo ""
    else
        echo "❌ Migration failed"
        echo "Check the response above for details"
        exit 1
    fi
    
    # Clean up response file
    rm response.json
else
    echo "❌ Failed to invoke Lambda"
    exit 1
fi

echo "✅ Database is ready!"
echo ""
echo "🎯 Next steps:"
echo "1. Test API: curl <api-url>/health"
echo "2. Test login: curl -X POST <api-url>/api/auth/login -d '{\"username\":\"receiving1\",\"password\":\"password123\"}'"
echo "3. Update frontend with API URL"
echo ""
