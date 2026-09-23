# IAM Policy Templates for SSM Parameter Store Access

This directory contains IAM policy templates for managing access to lakehouse-agent SSM parameters.

## Policy Files

### 1. lakehouse-ssm-read-policy.json

**Purpose**: Read-only access to SSM parameters for application runtime

**Use Cases**:
- Lambda function execution roles
- ECS task execution roles
- EC2 instance profiles
- AgentCore Runtime roles
- Any service that needs to read configuration

**Permissions Granted**:
- `ssm:GetParameter` - Read individual parameters
- `ssm:GetParametersByPath` - Bulk read parameters with lh_ prefix
- `kms:Decrypt` - Decrypt SecureString parameters
- `sts:GetCallerIdentity` - Get AWS account ID for auto-detection

**Security Features**:
- Restricted to `lh_*` parameters only
- KMS decrypt only via SSM service
- Region-restricted (uses ${AWS_REGION} placeholder)

### 2. lakehouse-ssm-admin-policy.json

**Purpose**: Full management access for DevOps and migration operations

**Use Cases**:
- DevOps engineers managing configuration
- CI/CD pipelines deploying infrastructure
- Migration utility execution
- Parameter backup and restore operations

**Permissions Granted**:
- All read permissions from read-only policy
- `ssm:PutParameter` - Create/update parameters
- `ssm:DeleteParameter` - Remove parameters
- `ssm:GetParameterHistory` - View parameter versions
- `ssm:AddTagsToResource` - Tag parameters
- `ssm:DescribeParameters` - List all parameters

**Security Features**:
- Restricted to `lh_*` parameters only
- Region-restricted (uses ${AWS_REGION} placeholder)
- Includes tagging permissions for organization

## Usage Instructions

### Creating Policies in AWS

**Option 1: Using AWS CLI**

```bash
# Set your AWS region
export AWS_REGION=us-east-1

# Create read-only policy
aws iam create-policy \
  --policy-name LakehouseSSMReadPolicy \
  --policy-document file://lakehouse-ssm-read-policy.json \
  --description "Read-only access to lakehouse-agent SSM parameters"

# Create admin policy
aws iam create-policy \
  --policy-name LakehouseSSMAdminPolicy \
  --policy-document file://lakehouse-ssm-admin-policy.json \
  --description "Full management access to lakehouse-agent SSM parameters"
```

**Option 2: Using AWS Console**

1. Navigate to IAM → Policies → Create policy
2. Click "JSON" tab
3. Copy contents of policy file
4. Replace `${AWS_REGION}` with your region (e.g., `us-east-1`)
5. Click "Next: Tags"
6. Add tags (optional):
   - Key: `Application`, Value: `lakehouse-agent`
   - Key: `Environment`, Value: `production`
7. Click "Next: Review"
8. Enter policy name and description
9. Click "Create policy"

### Attaching Policies to Roles

**Attach to Lambda Execution Role**:
```bash
# For application runtime (read-only)
aws iam attach-role-policy \
  --role-name lakehouse-mcp-server-role \
  --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMReadPolicy

# For migration utility (admin)
aws iam attach-role-policy \
  --role-name lakehouse-admin-role \
  --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMAdminPolicy
```

**Attach to IAM User**:
```bash
# For DevOps engineer
aws iam attach-user-policy \
  --user-name devops-engineer \
  --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMAdminPolicy
```

**Attach to IAM Group**:
```bash
# Create group for lakehouse admins
aws iam create-group --group-name lakehouse-admins

# Attach policy to group
aws iam attach-group-policy \
  --group-name lakehouse-admins \
  --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMAdminPolicy

# Add users to group
aws iam add-user-to-group \
  --group-name lakehouse-admins \
  --user-name devops-engineer
```

### Verifying Policy Attachment

```bash
# List policies attached to a role
aws iam list-attached-role-policies --role-name lakehouse-mcp-server-role

# List policies attached to a user
aws iam list-attached-user-policies --user-name devops-engineer

# Get policy details
aws iam get-policy \
  --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMReadPolicy

# Get policy version (to see actual permissions)
aws iam get-policy-version \
  --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMReadPolicy \
  --version-id v1
```

## Policy Customization

### Restricting to Specific Region

Replace `${AWS_REGION}` placeholder with your specific region:

```json
"Condition": {
  "StringEquals": {
    "aws:RequestedRegion": "us-east-1"
  }
}
```

### Adding MFA Requirement

Add MFA requirement for admin operations:

```json
{
  "Sid": "SSMParameterManagement",
  "Effect": "Allow",
  "Action": ["ssm:PutParameter", "ssm:DeleteParameter"],
  "Resource": "arn:aws:ssm:*:*:parameter/lh_*",
  "Condition": {
    "Bool": {
      "aws:MultiFactorAuthPresent": "true"
    }
  }
}
```

### Restricting by Environment Tag

Allow access only to parameters tagged with specific environment:

```json
{
  "Sid": "SSMParameterRead",
  "Effect": "Allow",
  "Action": ["ssm:GetParameter"],
  "Resource": "arn:aws:ssm:*:*:parameter/lh_*",
  "Condition": {
    "StringEquals": {
      "ssm:ResourceTag/Environment": "production"
    }
  }
}
```

### Time-Based Access

Restrict access to business hours:

```json
{
  "Sid": "SSMParameterManagement",
  "Effect": "Allow",
  "Action": ["ssm:PutParameter"],
  "Resource": "arn:aws:ssm:*:*:parameter/lh_*",
  "Condition": {
    "DateGreaterThan": {"aws:CurrentTime": "2024-01-01T09:00:00Z"},
    "DateLessThan": {"aws:CurrentTime": "2024-12-31T17:00:00Z"}
  }
}
```

## Testing Policies

### Test Read Access

```bash
# Assume role with read-only policy
aws sts assume-role \
  --role-arn arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-mcp-server-role \
  --role-session-name test-session

# Export credentials
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...

# Test reading parameter
aws ssm get-parameter --name lh_s3_bucket_name

# Test reading all parameters
aws ssm get-parameters-by-path --path /lh_ --recursive

# Test writing (should fail)
aws ssm put-parameter \
  --name lh_test \
  --value "test" \
  --type String
# Expected: AccessDeniedException
```

### Test Admin Access

```bash
# Assume role with admin policy
aws sts assume-role \
  --role-arn arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-admin-role \
  --role-session-name admin-session

# Test creating parameter
aws ssm put-parameter \
  --name lh_test_param \
  --value "test-value" \
  --type String

# Test updating parameter
aws ssm put-parameter \
  --name lh_test_param \
  --value "updated-value" \
  --type String \
  --overwrite

# Test deleting parameter
aws ssm delete-parameter --name lh_test_param
```

## Troubleshooting

### AccessDeniedException

**Error**: `User: arn:aws:iam::XXXXXXXXXXXX:role/MyRole is not authorized to perform: ssm:GetParameter`

**Solutions**:
1. Verify policy is attached to role:
   ```bash
   aws iam list-attached-role-policies --role-name MyRole
   ```

2. Check policy document has correct permissions:
   ```bash
   aws iam get-policy-version \
     --policy-arn arn:aws:iam::XXXXXXXXXXXX:policy/LakehouseSSMReadPolicy \
     --version-id v1
   ```

3. Verify parameter name starts with `lh_`:
   ```bash
   aws ssm describe-parameters --filters "Key=Name,Values=lh_"
   ```

### KMS Decrypt Error

**Error**: `User is not authorized to perform: kms:Decrypt`

**Solutions**:
1. Verify KMS permission in policy includes condition:
   ```json
   "Condition": {
     "StringEquals": {
       "kms:ViaService": "ssm.*.amazonaws.com"
     }
   }
   ```

2. Check KMS key policy allows your role:
   ```bash
   aws kms get-key-policy \
     --key-id alias/aws/ssm \
     --policy-name default
   ```

### Region Mismatch

**Error**: Parameters not found or access denied

**Solutions**:
1. Verify you're in the correct region:
   ```bash
   aws configure get region
   ```

2. Check parameter exists in that region:
   ```bash
   aws ssm get-parameter --name lh_s3_bucket_name --region us-east-1
   ```

## Best Practices

1. **Use Read-Only Policy by Default**: Grant admin access only when necessary
2. **Implement Least Privilege**: Start with minimal permissions and add as needed
3. **Use IAM Groups**: Manage permissions via groups, not individual users
4. **Enable MFA**: Require MFA for admin operations
5. **Regular Audits**: Review policy attachments quarterly
6. **Tag Resources**: Use tags for organization and conditional access
7. **Monitor Access**: Set up CloudWatch alarms for unauthorized access
8. **Document Changes**: Keep change log for policy modifications
9. **Test Policies**: Always test in non-production first
10. **Version Control**: Store policy files in git for change tracking

## Related Documentation

- [SSM Configuration Guide](../README.md#configuration-management-with-aws-systems-manager-ssm)
- [Security Setup](../SECURITY_SETUP.md#ssm-parameter-store-security)
- [Migration Guide](../README.md#migration-from-env-to-ssm)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS SSM Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
