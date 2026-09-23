# IAM Roles Setup for Lakehouse Agent

This directory contains scripts to set up IAM roles for tenant-based access control in the lakehouse agent system.

## Overview

The setup script creates two IAM roles that enable fine-grained access control for different tenants:
- `lakehouse-tenant-1`: IAM role for tenant 1
- `lakehouse-tenant-2`: IAM role for tenant 2

Each role is configured with:
- Trust policy allowing Amazon Bedrock and the current AWS account to assume the role
- Inline policy granting permissions for Athena queries and S3 data access
- Tags for identification and management

## Prerequisites

Before running the setup script, ensure:

1. **AWS Credentials**: You have valid AWS credentials configured with permissions to:
   - Create IAM roles
   - Attach IAM policies
   - Write to SSM Parameter Store

2. **S3 Bucket**: The Athena setup has been completed and the S3 bucket name is stored in SSM Parameter Store at `/app/lakehouse-agent/s3-bucket-name`

3. **Python Dependencies**: Install required packages:
   ```bash
   pip install boto3
   ```

## Usage

### Basic Usage

Run the script to create both tenant roles:

```bash
python setup_iam_roles.py
```

This will:
1. Retrieve the S3 bucket name from SSM Parameter Store
2. Create `lakehouse-tenant-1` and `lakehouse-tenant-2` roles
3. Attach Athena and S3 access policies to each role
4. Store role ARNs in SSM Parameter Store

### Specify Account ID

Optionally specify the AWS account ID for the trust policy:

```bash
python setup_iam_roles.py --account-id 123456789012
```

If not provided, the script uses the current account ID from AWS STS.

## Created Resources

### IAM Roles

The script creates two IAM roles with the following configuration:

**Role Names:**
- `lakehouse-tenant-1`
- `lakehouse-tenant-2`

**Trust Policy:**
- Allows Amazon Bedrock service to assume the role
- Allows the current AWS account root to assume the role

**Permissions:**
- Athena query execution and management
- AWS Glue catalog access (databases, tables, partitions)
- S3 read access to the lakehouse data bucket
- S3 read/write access to Athena query results

### SSM Parameters

Role ARNs are stored in SSM Parameter Store:
- `/app/lakehouse-agent/roles/lakehouse-tenant-1`
- `/app/lakehouse-agent/roles/lakehouse-tenant-2`

## Permissions Granted

Each tenant role has the following permissions:

### Athena Access
- Start, stop, and get query executions
- Access workgroups and data catalogs
- List databases and table metadata

### Glue Access
- Get database and table information
- Get partitions
- List databases and tables

### S3 Access
- Read objects from the lakehouse data bucket
- List bucket contents
- Write query results to `athena-results/` prefix
- Get bucket location

## Integration with Gateway

These roles are used by the AgentCore Gateway for fine-grained access control:

1. The gateway intercepts requests and determines the tenant
2. It assumes the appropriate tenant role based on the user context
3. Athena queries are executed with the tenant role's permissions
4. Row-level security is enforced through the role's credentials

## Verification

After running the setup, verify the roles were created:

```bash
# List the roles
aws iam get-role --role-name lakehouse-tenant-1
aws iam get-role --role-name lakehouse-tenant-2

# Check SSM parameters
aws ssm get-parameter --name /app/lakehouse-agent/roles/lakehouse-tenant-1
aws ssm get-parameter --name /app/lakehouse-agent/roles/lakehouse-tenant-2
```

## Cleanup

To remove the created roles:

```bash
# Delete inline policies
aws iam delete-role-policy --role-name lakehouse-tenant-1 --policy-name lakehouse-tenant-1-athena-s3-access
aws iam delete-role-policy --role-name lakehouse-tenant-2 --policy-name lakehouse-tenant-2-athena-s3-access

# Delete roles
aws iam delete-role --role-name lakehouse-tenant-1
aws iam delete-role --role-name lakehouse-tenant-2

# Delete SSM parameters
aws ssm delete-parameter --name /app/lakehouse-agent/roles/lakehouse-tenant-1
aws ssm delete-parameter --name /app/lakehouse-agent/roles/lakehouse-tenant-2
```

## Troubleshooting

### Error: NoSuchEntity - S3 bucket parameter not found

If the script cannot find the S3 bucket name in SSM:
1. Ensure you've run the Athena setup script first
2. Or manually create the SSM parameter:
   ```bash
   aws ssm put-parameter \
     --name /app/lakehouse-agent/s3-bucket-name \
     --value "your-bucket-name" \
     --type String
   ```

### Error: AccessDenied when creating roles

Ensure your AWS credentials have the following permissions:
- `iam:CreateRole`
- `iam:PutRolePolicy`
- `iam:GetRole`
- `ssm:PutParameter`

### Role already exists

If the roles already exist, the script will skip creation and use the existing roles. To recreate them, delete the roles first using the cleanup commands above.

## Security Considerations

1. **Least Privilege**: The roles are configured with minimal permissions needed for Athena queries and S3 access
2. **Trust Policy**: Only Amazon Bedrock and the current account can assume these roles
3. **Resource Scoping**: Permissions are scoped to specific S3 buckets and Athena resources
4. **Audit Trail**: All role assumptions are logged in CloudTrail

## Next Steps

After setting up the IAM roles:
1. Configure the AgentCore Gateway to use these roles for tenant isolation
2. Set up the request interceptor to map users to tenant roles
3. Test access control by querying data as different tenants
