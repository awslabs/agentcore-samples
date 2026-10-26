# Permissions

Each policy in this document represents the minimum permissions required for the operation it
describes. No policy grants `*` on a service. Administrator permissions are required only when
creating target registries and deploying the optional AWS Glue engine, both of which are one-time
operations.

Two IAM namespaces appear throughout this document. Reading preview data requires permissions in
the `bedrock-agentcore` namespace. Writing target data requires permissions in the `agent-registry`
namespace. Both namespaces are required during the migration window.

**Contents**

- [Permission requirements by role](#permission-requirements-by-role)
- [Running the migration locally](#running-the-migration-locally)
- [Creating target registries](#creating-target-registries)
- [Synchronized records: update the role's trust policy](#synchronized-records-update-the-roles-trust-policy)
- [Deploying the Glue engine](#deploying-the-glue-engine)
- [The engine's execution role](#the-engines-execution-role)
- [Registries in another account](#registries-in-another-account)
- [Scoping action lists](#scoping-action-lists)

## Permission requirements by role

| Role | Required permissions |
| --- | --- |
| Running `run` locally or in AWS CloudShell | [Running the migration locally](#running-the-migration-locally) |
| Creating the target registry to migrate into | [Creating target registries](#creating-target-registries) (one-time) |
| Migrating records that use Synchronize with an IAM role | [Synchronized records: update the role's trust policy](#synchronized-records-update-the-roles-trust-policy) |
| Deploying the optional Glue engine | [Deploying the Glue engine](#deploying-the-glue-engine) (one-time) |
| Supplying the engine's execution role instead of having the stack create it | [The engine's execution role](#the-engines-execution-role) |
| Migrating a registry in another AWS account | [Registries in another account](#registries-in-another-account) |

## Running the migration locally

The following policy grants the permissions required by `init`, `check`, `run`, and `report` in
local mode. The policy allows reading all preview records, writing all target records, and calling
`sts:GetCallerIdentity`. No other read or write access is granted.

Replace `<region>`, `<account-id>`, `<preview-registry-id>`, and `<new-registry-id>` with the
appropriate values. A wildcard (`*`) can be used in place of a registry ID while the registry IDs
are not yet known.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ConfirmCallerIdentity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    },
    {
      "Sid": "ReadPreviewRegistries",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:ListRegistryRecords",
        "bedrock-agentcore:GetRegistryRecord",
        "bedrock-agentcore:GetRegistry"
      ],
      "Resource": [
        "arn:aws:bedrock-agentcore:<region>:<account-id>:registry/<preview-registry-id>",
        "arn:aws:bedrock-agentcore:<region>:<account-id>:registry/<preview-registry-id>/record/*"
      ]
    },
    {
      "Sid": "WriteTargetRegistries",
      "Effect": "Allow",
      "Action": [
        "agent-registry:ListRegistryRecords",
        "agent-registry:GetRegistryRecord",
        "agent-registry:CreateRegistryRecord",
        "agent-registry:UpdateRegistryRecord",
        "agent-registry:SubmitRegistryRecordForApproval",
        "agent-registry:UpdateRegistryRecordStatus"
      ],
      "Resource": [
        "arn:aws:agent-registry:<region>:<account-id>:registry/<new-registry-id>",
        "arn:aws:agent-registry:<region>:<account-id>:registry/<new-registry-id>/record/*"
      ]
    }
  ]
}
```

The following table describes why each action is required:

| Action | Purpose |
| --- | --- |
| `sts:GetCallerIdentity` | The `init` command derives the account ID from credentials. The `check` command displays the identity that will perform the migration |
| `bedrock-agentcore:ListRegistryRecords` | Reads the source registry one page at a time |
| `bedrock-agentcore:GetRegistryRecord` | Retrieves descriptor content, which is not included in list responses |
| `bedrock-agentcore:GetRegistry` | The `init` and `target-config` commands read the preview registry's authorizer and approval settings to derive the target registry configuration |
| `agent-registry:CreateRegistryRecord` | Creates the migrated record in the target registry |
| `agent-registry:ListRegistryRecords`, `agent-registry:GetRegistryRecord` | Detects records that already exist so that a re-run updates rather than duplicates them |
| `agent-registry:UpdateRegistryRecord` | Updates a previously migrated record when the source has changed |
| `agent-registry:SubmitRegistryRecordForApproval`, `agent-registry:UpdateRegistryRecordStatus` | Sets the migrated record to the same status it holds in the preview registry. Without these actions, an approved record is created in `DRAFT` status and is not returned by data-plane search or browsing APIs |

None of these actions can delete a record in either namespace.

**Dry run.** Running `run` without `--live` does not call any write APIs. To grant read-only access,
omit the `WriteTargetRegistries` statement, with the exception of
`agent-registry:ListRegistryRecords`, which the `check` command uses to verify that the target
registry is reachable.

## Creating target registries

Registries are created once per registry, before any records can be loaded into them. The `init`
command does this for you — it derives each registry's settings from the preview registry it
replaces, creates it once you confirm, waits for it to become `READY`, and records the generated ID.
`target-config --create` does the same for a mapping added later, and answering `n` at the prompt
prints the equivalent AWS CLI command to run yourself instead.

Either way, creating a registry requires permissions beyond those needed to migrate records. These
are one-time permissions: a credential that only migrates records does not need them.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CreateTargetRegistry",
      "Effect": "Allow",
      "Action": ["agent-registry:CreateRegistry", "agent-registry:GetRegistry"],
      "Resource": "*"
    },
    {
      "Sid": "WorkloadIdentityForRegistryCreation",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:CreateWorkloadIdentity",
        "bedrock-agentcore:GetWorkloadIdentity",
        "bedrock-agentcore:DeleteWorkloadIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

The `WorkloadIdentityForRegistryCreation` statement is required because workload identities and
OAuth credential providers remain in the `bedrock-agentcore` namespace in the new version. Creating a
registry also creates a workload identity. A policy without this statement fails with a workload
identity error rather than a registry permission error.

## Synchronized records: update the role's trust policy

Records that use the **Synchronize** feature with the **IAM role** credential type name a role that
the registry service itself assumes to fetch the record's URL. The service principal changed in the new version,
so that role's trust policy must be updated.

This applies only to records that use Synchronize **and** IAM role credentials. Records using an
OAuth client, or no authorization, are unaffected.

**Before (preview):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**After (new version):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "agent-registry.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Until the trust policy is updated, the registry cannot assume the role and synchronization fails.

**Why this matters for the migration.** The migration copies each descriptor's
`source.fromUrl.credentialProviderConfigurations` into the target record unchanged, because the role and
its configuration are the customer's, not the tool's. A record that synchronized correctly in
preview therefore arrives in the target registry still pointing at the same role — and fails to synchronize until
that role trusts the new principal. Update the trust policy **before** the live load.

This is not something `check` can detect: the tool never assumes that role. It belongs to the
registry service, and is used asynchronously after the record is created.

**If a record has already failed for this reason,** the target record exists in `CREATE_FAILED` status.
The migration refuses to overwrite a record in a failure status, so fixing the trust policy and
re-running the load reports the same error. Update the trust policy, delete the `CREATE_FAILED`
record from the target registry, then re-run the load. A re-extract is only needed if the role ARN
inside the preview record itself was wrong, since staged records are immutable.

## Deploying the Glue engine

The following permissions are required only if deploying the optional Glue engine. The `deploy`
and `destroy` commands use the AWS CDK, which provisions resources through AWS CloudFormation.

| Service | Purpose |
| --- | --- |
| AWS CloudFormation | The engine stack and its termination protection |
| Amazon S3 | The staging bucket and the CDK asset bucket used during deployment |
| AWS Identity and Access Management | The engine's execution role and attached policy (not required when `createIamRoles: false`) |
| AWS Glue | Two Glue 5.0 jobs, a workflow, and a trigger |
| AWS Systems Manager Parameter Store | Three configuration parameters read by the Glue jobs |
| AWS Lambda | CDK bucket-deployment custom resource that uploads the engine package |
| AWS Security Token Service | CDK bootstrap role assumption |

Scoping deployment permissions to a least-privilege policy depends on the CDK bootstrap
configuration and cannot be defined generically. Use one of the following approaches:

- **CDK bootstrap execution role.** This is the role that `cdk deploy` is designed to assume.
- **Administrator access.** Appropriate for a one-time deployment in a development account.
- **`createIamRoles: false`.** Supply a customer-managed role in `engine.glueRoleArn`. The stack
  creates no IAM resources. See [The engine's execution role](#the-engines-execution-role).

**Running a migration on a deployed engine.** Submitting a migration run to an already-deployed
engine with `run --glue` requires fewer permissions than deployment:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StartAndMonitorMigrationJobs",
      "Effect": "Allow",
      "Action": ["glue:StartJobRun", "glue:GetJobRun"],
      "Resource": "arn:aws:glue:<region>:<account-id>:job/*"
    },
    {
      "Sid": "DescribeDeployedEngine",
      "Effect": "Allow",
      "Action": "cloudformation:DescribeStacks",
      "Resource": "arn:aws:cloudformation:<region>:<account-id>:stack/AgentRegistryMigrationEngine/*"
    },
    {
      "Sid": "ReadRunConfiguration",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
      "Resource": "arn:aws:ssm:<region>:<account-id>:parameter/agent-registry-migration/*"
    },
    {
      "Sid": "ReadRunReports",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::<staging-bucket>",
        "arn:aws:s3:::<staging-bucket>/*"
      ]
    }
  ]
}
```

## The engine's execution role

When `engine.createIamRoles` is set to `false`, the CloudFormation stack creates no IAM resources.
The role specified in `engine.glueRoleArn` must have the following policy attached. This is
identical to what the stack would create when `createIamRoles` is `true`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StagingBucketAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:AbortMultipartUpload"
      ],
      "Resource": [
        "arn:aws:s3:::<staging-bucket>",
        "arn:aws:s3:::<staging-bucket>/*"
      ]
    },
    {
      "Sid": "RunConfigurationAccess",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
      "Resource": "arn:aws:ssm:<region>:<account-id>:parameter/agent-registry-migration/*"
    },
    {
      "Sid": "JobLogsAccess",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:<region>:<account-id>:log-group:/aws-glue/*"
    },
    {
      "Sid": "ReadPreviewRegistries",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:ListRegistryRecords",
        "bedrock-agentcore:GetRegistryRecord",
        "bedrock-agentcore:GetRegistry"
      ],
      "Resource": [
        "arn:aws:bedrock-agentcore:<region>:<account-id>:registry/<preview-registry-id>",
        "arn:aws:bedrock-agentcore:<region>:<account-id>:registry/<preview-registry-id>/record/*"
      ]
    },
    {
      "Sid": "WriteTargetRegistries",
      "Effect": "Allow",
      "Action": [
        "agent-registry:ListRegistryRecords",
        "agent-registry:GetRegistryRecord",
        "agent-registry:CreateRegistryRecord",
        "agent-registry:UpdateRegistryRecord",
        "agent-registry:SubmitRegistryRecordForApproval",
        "agent-registry:UpdateRegistryRecordStatus"
      ],
      "Resource": [
        "arn:aws:agent-registry:<region>:<account-id>:registry/<new-registry-id>",
        "arn:aws:agent-registry:<region>:<account-id>:registry/<new-registry-id>/record/*"
      ]
    }
  ]
}
```

The role's trust policy must allow the AWS Glue service to assume it:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "glue.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

To verify that the deployment creates no IAM resources when `createIamRoles` is `false`, run the
following command and confirm the output is `0`:

```bash
npx cdk synth --all -c config=config/migration.customer-managed-role.example.json | grep -c "AWS::IAM::"
# Expected output: 0
```

## Registries in another account

A registry in an account other than the one running the migration requires an IAM role in that
account for the migration to assume. Specify the role ARN in the registry pair configuration as
`source.roleArn` or `target.roleArn`, and grant the migration caller permission to assume it:

```json
{
  "Sid": "AssumeRemoteAccountRole",
  "Effect": "Allow",
  "Action": "sts:AssumeRole",
  "Resource": "arn:aws:iam::<other-account-id>:role/<access-role-name>"
}
```

The role in the remote account must have the appropriate preview-read or target-write permissions from
this document — whichever side it represents — and must trust the account running the migration.
Use an `externalId` in the registry pair configuration and require it in the trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::<migration-account-id>:root" },
      "Action": "sts:AssumeRole",
      "Condition": { "StringEquals": { "sts:ExternalId": "<external-id>" } }
    }
  ]
}
```

When `createIamRoles` is `true`, the `deploy` command creates a `RegistryAccess-<account-id>`
CloudFormation stack for each remote account. Deploy that stack in the remote account to create
the access role. The migration tool resolves the role by name without requiring the ARN to be
specified explicitly.

## Scoping action lists

The preview read and target write action lists are configuration values, not code. They can be
restricted without modifying the tool:

```jsonc
{
  "iam": {
    // Suitable for a dry run, or for an operator with read-only access requirements.
    "previewReadActions": ["bedrock-agentcore:ListRegistryRecords", "bedrock-agentcore:GetRegistryRecord"],
    "targetWriteActions": ["agent-registry:ListRegistryRecords", "agent-registry:CreateRegistryRecord"]
  }
}
```

The `check` command validates permissions before a run by assuming each configured role and calling
each registry. An incomplete policy surfaces as a named failing check within seconds rather than as
an `AccessDenied` error partway through a migration.
