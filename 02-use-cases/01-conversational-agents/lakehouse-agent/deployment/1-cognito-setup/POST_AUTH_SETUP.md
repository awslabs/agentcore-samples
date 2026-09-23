# Cognito Post-Authentication Lambda Trigger Setup

This directory contains the setup for logging user authentication events to DynamoDB using a Cognito Post-Authentication Lambda trigger.

## Overview

When users successfully authenticate with Cognito, a Lambda function is automatically triggered to log the authentication event to a DynamoDB table. This provides:

- **Audit Trail**: Track all user logins with timestamps
- **Security Monitoring**: Detect unusual login patterns
- **Analytics**: Understand user behavior and access patterns
- **Compliance**: Meet regulatory requirements for access logging

## Architecture

```
User Login → Cognito User Pool → Post-Auth Trigger → Lambda Function → DynamoDB Table
```

## Components

### 1. DynamoDB Table: `lakehouse_user_login_audit`

Stores login audit records with the following schema:

- **Primary Key**: `user_id` (HASH) - User's email/username
- **Sort Key**: `login_timestamp` (RANGE) - ISO 8601 timestamp
- **Attributes**:
  - `user_pool_id` - Cognito User Pool ID
  - `email` - User's email address
  - `email_verified` - Email verification status
  - `cognito_username` - Cognito username
  - `groups` - User's Cognito groups (JSON string)
  - `client_id` - App client ID used for authentication
  - `source_ip` - IP address of the login request
  - `user_agent` - User agent string
  - `event_type` - Always "post_authentication"
  - `ttl` - Time-to-live (90 days from login)

### 2. Lambda Function: `lakehouse-cognito-post-auth`

- **Runtime**: Python 3.11
- **Handler**: `post_auth_lambda.lambda_handler`
- **Timeout**: 10 seconds
- **Memory**: 256 MB
- **Environment Variables**:
  - `LOGIN_AUDIT_TABLE`: DynamoDB table name

### 3. IAM Role: `lakehouse-cognito-post-auth-role`

Permissions:
- DynamoDB: `PutItem`, `GetItem`, `Query` on the audit table
- CloudWatch Logs: Create log groups and streams

## Deployment

### Step 1: Deploy Lambda and DynamoDB

Run the deployment script:

```bash
cd 02-use-cases/lakehouse-agent/deployment/cognito-setup
bash deploy_post_auth_lambda.sh
```

This script will:
1. Create the DynamoDB table with TTL enabled
2. Create the IAM role for Lambda
3. Package and deploy the Lambda function
4. Grant Cognito permission to invoke the Lambda

### Step 2: Configure Cognito Trigger

After deploying the Lambda, configure the Cognito User Pool to use it:

```bash
python setup_cognito.py --add-post-auth-trigger
```

This will update the User Pool's Lambda configuration to invoke the post-auth function after successful authentication.

## Verification

### Test the Setup

1. Login with a test user:
   ```bash
   # Use the Streamlit UI or authenticate via CLI
   aws cognito-idp admin-initiate-auth \
     --user-pool-id <USER_POOL_ID> \
     --client-id <CLIENT_ID> \
     --auth-flow ADMIN_USER_PASSWORD_AUTH \
     --auth-parameters USERNAME=policyholder001@example.com,PASSWORD=<password>
   ```

2. Check DynamoDB for the login record:
   ```bash
   aws dynamodb query \
     --table-name lakehouse_user_login_audit \
     --key-condition-expression "user_id = :uid" \
     --expression-attribute-values '{":uid":{"S":"policyholder001@example.com"}}' \
     --limit 5 \
     --scan-index-forward false
   ```

### View Lambda Logs

Check CloudWatch Logs for the Lambda function:

```bash
aws logs tail /aws/lambda/lakehouse-cognito-post-auth --follow
```

## Query Examples

### Get all logins for a specific user

```python
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('lakehouse_user_login_audit')

response = table.query(
    KeyConditionExpression=Key('user_id').eq('policyholder001@example.com'),
    ScanIndexForward=False,  # Most recent first
    Limit=10
)

for item in response['Items']:
    print(f"{item['login_timestamp']}: {item['email']} from {item['source_ip']}")
```

### Get recent logins across all users

```python
import boto3

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('lakehouse_user_login_audit')

# Scan for recent logins (use with caution on large tables)
response = table.scan(
    Limit=20
)

for item in response['Items']:
    print(f"{item['user_id']} - {item['login_timestamp']}")
```

## Data Retention

The DynamoDB table has TTL (Time-To-Live) enabled on the `ttl` attribute. Records are automatically deleted 90 days after the login event. You can adjust this in the deployment script:

```python
# In post_auth_lambda.py, change the TTL calculation:
'ttl': int(datetime.utcnow().timestamp()) + (90 * 24 * 60 * 60)  # 90 days
```

## Security Considerations

1. **Error Handling**: The Lambda function catches all exceptions and logs them without failing the authentication. This ensures users can still login even if logging fails.

2. **PII Data**: The table stores email addresses and IP addresses. Ensure compliance with data protection regulations (GDPR, CCPA, etc.).

3. **Access Control**: Restrict access to the DynamoDB table using IAM policies. Only authorized personnel should be able to query login records.

4. **Encryption**: Consider enabling encryption at rest for the DynamoDB table for sensitive environments.

## Troubleshooting

### Lambda not being triggered

1. Check Cognito User Pool configuration:
   ```bash
   aws cognito-idp describe-user-pool --user-pool-id <USER_POOL_ID> \
     --query 'UserPool.LambdaConfig'
   ```

2. Verify Lambda has permission:
   ```bash
   aws lambda get-policy --function-name lakehouse-cognito-post-auth
   ```

### Records not appearing in DynamoDB

1. Check Lambda logs for errors:
   ```bash
   aws logs tail /aws/lambda/lakehouse-cognito-post-auth --since 10m
   ```

2. Verify IAM role has DynamoDB permissions:
   ```bash
   aws iam get-role-policy \
     --role-name lakehouse-cognito-post-auth-role \
     --policy-name lakehouse-cognito-post-auth-role-policy
   ```

## Cleanup

To remove the post-authentication setup:

```bash
# Remove Lambda trigger from Cognito
aws cognito-idp update-user-pool \
  --user-pool-id <USER_POOL_ID> \
  --lambda-config '{}'

# Delete Lambda function
aws lambda delete-function --function-name lakehouse-cognito-post-auth

# Delete IAM role
aws iam delete-role-policy \
  --role-name lakehouse-cognito-post-auth-role \
  --policy-name lakehouse-cognito-post-auth-role-policy
aws iam delete-role --role-name lakehouse-cognito-post-auth-role

# Delete DynamoDB table
aws dynamodb delete-table --table-name lakehouse_user_login_audit
```

## Cost Estimation

- **DynamoDB**: Pay-per-request pricing (~$1.25 per million writes)
- **Lambda**: Free tier covers 1M requests/month, then $0.20 per 1M requests
- **CloudWatch Logs**: $0.50 per GB ingested

For typical usage (100 logins/day), monthly cost is negligible (<$1).
