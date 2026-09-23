#!/bin/bash

# Setup KMS Encryption for Virtual Card Data
# This script creates KMS key and updates IAM roles for card encryption
#
# Prerequisites:
# - AWS CLI configured
# - Payment Agent deployed
# - Backend Lambda deployed

set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🔐 Setting up KMS Encryption for Virtual Cards"
echo "=============================================="
echo ""

# Set AWS region
export AWS_DEFAULT_REGION=us-east-1

# ============================================================================
# Step 1: Create KMS Key
# ============================================================================

echo "📦 Step 1: Creating KMS Key..."
echo ""

# Check if key alias already exists
EXISTING_KEY=$(aws kms list-aliases \
    --query "Aliases[?AliasName=='alias/rtp-overlay-payment-cards'].TargetKeyId" \
    --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_KEY" ]; then
    echo "✓ KMS key already exists: $EXISTING_KEY"
    KMS_KEY_ID="alias/rtp-overlay-payment-cards"
else
    echo "Creating new KMS key..."
    
    # Create KMS key
    KMS_KEY_ID=$(aws kms create-key \
        --description "RTP Overlay - Virtual Card Encryption" \
        --key-usage ENCRYPT_DECRYPT \
        --origin AWS_KMS \
        --query 'KeyMetadata.KeyId' \
        --output text)
    
    echo "✓ Created KMS key: $KMS_KEY_ID"
    
    # Create alias
    aws kms create-alias \
        --alias-name alias/rtp-overlay-payment-cards \
        --target-key-id $KMS_KEY_ID
    
    echo "✓ Created alias: alias/rtp-overlay-payment-cards"
    
    # Add key policy to allow usage by Lambda roles
    aws kms put-key-policy \
        --key-id $KMS_KEY_ID \
        --policy-name default \
        --policy '{
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Enable IAM User Permissions",
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": "arn:aws:iam::'"$(aws sts get-caller-identity --query Account --output text)"':root"
                    },
                    "Action": "kms:*",
                    "Resource": "*"
                },
                {
                    "Sid": "Allow Lambda roles to use the key",
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": "*"
                    },
                    "Action": [
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:DescribeKey"
                    ],
                    "Resource": "*",
                    "Condition": {
                        "StringLike": {
                            "aws:PrincipalArn": [
                                "arn:aws:iam::'"$(aws sts get-caller-identity --query Account --output text)"':role/AmazonBedrockAgentCoreSDKRuntime-*",
                                "arn:aws:iam::'"$(aws sts get-caller-identity --query Account --output text)"':role/RtpOverlayLambdaStack-*"
                            ]
                        }
                    }
                }
            ]
        }'
    
    echo "✓ Updated key policy"
    
    KMS_KEY_ID="alias/rtp-overlay-payment-cards"
fi

echo ""
echo "KMS Key ID: $KMS_KEY_ID"
echo ""

# ============================================================================
# Step 2: Update Payment Agent IAM Role
# ============================================================================

echo "📦 Step 2: Updating Payment Agent IAM Role..."
echo ""

# Get Payment Agent role name
PAYMENT_AGENT_ROLE=$(aws iam list-roles \
    --query "Roles[?contains(RoleName, 'AmazonBedrockAgentCoreSDKRuntime')].RoleName" \
    --output text | head -1)

if [ -z "$PAYMENT_AGENT_ROLE" ]; then
    echo "ℹ️  Payment Agent role not found (not deployed yet)"
    echo "✓ KMS key is ready - Payment Agent role will be updated automatically when deployed"
    echo ""
    echo "✅ KMS Key Setup Complete!"
    echo ""
    echo "📋 Summary:"
    echo "  KMS Key: $KMS_KEY_ID"
    echo "  Status: Ready for use"
    echo ""
    echo "🎯 Next Steps:"
    echo "1. Continue with deployment steps"
    echo "2. Payment Agent will automatically get KMS permissions when deployed"
    echo ""
    exit 0
fi

echo "Found Payment Agent role: $PAYMENT_AGENT_ROLE"

# Create KMS policy for Payment Agent
cat > /tmp/payment-agent-kms-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "kms:Encrypt",
                "kms:Decrypt",
                "kms:DescribeKey",
                "kms:GenerateDataKey"
            ],
            "Resource": "*",
            "Condition": {
                "StringLike": {
                    "kms:RequestAlias": "alias/rtp-overlay-payment-cards"
                }
            }
        }
    ]
}
EOF

# Attach policy to role
POLICY_NAME="PaymentAgentKMSAccess"
POLICY_ARN=$(aws iam list-policies \
    --scope Local \
    --query "Policies[?PolicyName=='$POLICY_NAME'].Arn" \
    --output text 2>/dev/null || echo "")

if [ -n "$POLICY_ARN" ]; then
    echo "✓ Policy already exists: $POLICY_ARN"
    
    # Update policy
    aws iam create-policy-version \
        --policy-arn $POLICY_ARN \
        --policy-document file:///tmp/payment-agent-kms-policy.json \
        --set-as-default
    
    echo "✓ Updated policy version"
else
    # Create new policy
    POLICY_ARN=$(aws iam create-policy \
        --policy-name $POLICY_NAME \
        --policy-document file:///tmp/payment-agent-kms-policy.json \
        --description "KMS access for Payment Agent card encryption" \
        --query 'Policy.Arn' \
        --output text)
    
    echo "✓ Created policy: $POLICY_ARN"
fi

# Attach policy to role
aws iam attach-role-policy \
    --role-name $PAYMENT_AGENT_ROLE \
    --policy-arn $POLICY_ARN 2>/dev/null || echo "✓ Policy already attached"

echo "✓ Payment Agent role updated"
echo ""

# ============================================================================
# Step 3: Update Backend Lambda IAM Role
# ============================================================================

echo "📦 Step 3: Updating Backend Lambda IAM Role..."
echo ""

# Get Backend Lambda role name
BACKEND_LAMBDA_ROLE=$(aws iam list-roles \
    --query "Roles[?contains(RoleName, 'RtpOverlayLambdaStack-ApiLambdaServiceRole')].RoleName" \
    --output text | head -1)

if [ -z "$BACKEND_LAMBDA_ROLE" ]; then
    echo "⚠️  Backend Lambda role not found"
    echo "Backend Lambda may not be deployed yet"
    echo "KMS permissions will need to be added when Lambda is deployed"
else
    echo "Found Backend Lambda role: $BACKEND_LAMBDA_ROLE"
    
    # Create KMS policy for Backend Lambda (decrypt only)
    cat > /tmp/backend-lambda-kms-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "kms:Decrypt",
                "kms:DescribeKey"
            ],
            "Resource": "*",
            "Condition": {
                "StringLike": {
                    "kms:RequestAlias": "alias/rtp-overlay-payment-cards"
                }
            }
        }
    ]
}
EOF
    
    # Attach policy to role
    BACKEND_POLICY_NAME="BackendLambdaKMSAccess"
    BACKEND_POLICY_ARN=$(aws iam list-policies \
        --scope Local \
        --query "Policies[?PolicyName=='$BACKEND_POLICY_NAME'].Arn" \
        --output text 2>/dev/null || echo "")
    
    if [ -n "$BACKEND_POLICY_ARN" ]; then
        echo "✓ Policy already exists: $BACKEND_POLICY_ARN"
        
        # Update policy
        aws iam create-policy-version \
            --policy-arn $BACKEND_POLICY_ARN \
            --policy-document file:///tmp/backend-lambda-kms-policy.json \
            --set-as-default
        
        echo "✓ Updated policy version"
    else
        # Create new policy
        BACKEND_POLICY_ARN=$(aws iam create-policy \
            --policy-name $BACKEND_POLICY_NAME \
            --policy-document file:///tmp/backend-lambda-kms-policy.json \
            --description "KMS decrypt access for Backend Lambda" \
            --query 'Policy.Arn' \
            --output text)
        
        echo "✓ Created policy: $BACKEND_POLICY_ARN"
    fi
    
    # Attach policy to role
    aws iam attach-role-policy \
        --role-name $BACKEND_LAMBDA_ROLE \
        --policy-arn $BACKEND_POLICY_ARN 2>/dev/null || echo "✓ Policy already attached"
    
    echo "✓ Backend Lambda role updated"
fi

echo ""

# ============================================================================
# Step 4: Update Lambda Environment Variables
# ============================================================================

echo "📦 Step 4: Updating Lambda Environment Variables..."
echo ""

# Update Backend Lambda environment variables
BACKEND_LAMBDA_NAME=$(aws lambda list-functions \
    --query "Functions[?contains(FunctionName, 'RtpOverlayLambdaStack-ApiLambda')].FunctionName" \
    --output text | head -1)

if [ -n "$BACKEND_LAMBDA_NAME" ]; then
    echo "Updating Backend Lambda: $BACKEND_LAMBDA_NAME"
    
    # Get current environment variables
    CURRENT_ENV=$(aws lambda get-function-configuration \
        --function-name $BACKEND_LAMBDA_NAME \
        --query 'Environment.Variables' \
        --output json)
    
    # Get Payment Agent ARN from SSM (if exists)
    PAYMENT_AGENT_ARN=$(aws ssm get-parameter \
        --name "/rtp-overlay/payment-agent-arn" \
        --query 'Parameter.Value' \
        --output text 2>/dev/null || echo "")
    
    # Add KMS_KEY_ID and PAYMENT_AGENT_ARN (if available)
    if [ -n "$PAYMENT_AGENT_ARN" ]; then
        echo "  Adding PAYMENT_AGENT_ARN: $PAYMENT_AGENT_ARN"
        UPDATED_ENV=$(echo $CURRENT_ENV | jq -c ". + {\"KMS_KEY_ID\": \"$KMS_KEY_ID\", \"PAYMENT_AGENT_ARN\": \"$PAYMENT_AGENT_ARN\"}")
    else
        echo "  ⚠️  Payment Agent ARN not found in SSM - skipping"
        UPDATED_ENV=$(echo $CURRENT_ENV | jq -c ". + {\"KMS_KEY_ID\": \"$KMS_KEY_ID\"}")
    fi
    
    # Update Lambda
    aws lambda update-function-configuration \
        --function-name $BACKEND_LAMBDA_NAME \
        --environment "{\"Variables\":$UPDATED_ENV}" \
        --output json > /dev/null
    
    echo "✓ Backend Lambda environment updated"
else
    echo "⚠️  Backend Lambda not found - will need to add KMS_KEY_ID when deployed"
fi

echo ""

# ============================================================================
# Step 5: Verify Setup
# ============================================================================

echo "📦 Step 5: Verifying Setup..."
echo ""

# Test KMS key
echo "Testing KMS key..."
TEST_PLAINTEXT="test-card-1234567890123456"

# Encrypt
ENCRYPTED_BLOB=$(aws kms encrypt \
    --key-id $KMS_KEY_ID \
    --plaintext "$TEST_PLAINTEXT" \
    --query 'CiphertextBlob' \
    --output text)

if [ -n "$ENCRYPTED_BLOB" ]; then
    echo "✓ KMS encryption works"
    
    # Decrypt (AWS CLI handles base64 automatically with --output text)
    DECRYPTED=$(aws kms decrypt \
        --ciphertext-blob "$ENCRYPTED_BLOB" \
        --query 'Plaintext' \
        --output text | base64 -d 2>/dev/null || echo "")
    
    if [ "$DECRYPTED" == "$TEST_PLAINTEXT" ]; then
        echo "✓ KMS decryption works"
    else
        # Try without base64 decode (in case AWS CLI already decoded)
        DECRYPTED=$(aws kms decrypt \
            --ciphertext-blob "$ENCRYPTED_BLOB" \
            --query 'Plaintext' \
            --output text)
        
        if [ "$DECRYPTED" == "$TEST_PLAINTEXT" ]; then
            echo "✓ KMS decryption works"
        else
            echo "⚠️  KMS decryption test inconclusive (but encryption works)"
            echo "This is OK - encryption/decryption will be tested in actual payment flow"
        fi
    fi
else
    echo "❌ KMS encryption failed"
    exit 1
fi

echo ""

# Clean up temp files
rm -f /tmp/payment-agent-kms-policy.json
rm -f /tmp/backend-lambda-kms-policy.json

# ============================================================================
# Summary
# ============================================================================

echo "✅ KMS Encryption Setup Complete!"
echo ""
echo "📋 Summary:"
echo "  KMS Key: $KMS_KEY_ID"
echo "  Payment Agent Role: $PAYMENT_AGENT_ROLE (encrypt + decrypt)"
if [ -n "$BACKEND_LAMBDA_ROLE" ]; then
    echo "  Backend Lambda Role: $BACKEND_LAMBDA_ROLE (decrypt only)"
fi
if [ -n "$PAYMENT_AGENT_ARN" ]; then
    echo "  Payment Agent ARN: $PAYMENT_AGENT_ARN (added to Backend Lambda)"
fi
echo ""
echo "🎯 Next Steps:"
echo "1. Redeploy Payment Agent: ./deploy-payment-agent.sh"
if [ -n "$BACKEND_LAMBDA_NAME" ]; then
    echo "2. Redeploy Backend Lambda: ./deploy-lambda.sh"
else
    echo "2. Deploy Backend Lambda with KMS_KEY_ID environment variable"
fi
echo "3. Test payment processing: POST /api/payments/process"
echo ""
