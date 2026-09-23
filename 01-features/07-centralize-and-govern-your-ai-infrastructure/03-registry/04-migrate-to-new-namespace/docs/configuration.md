# Configuration Reference

The `init` command generates a configuration file with values derived from your credentials and the
registries you specify. Most settings have defaults that work for a standard migration. This
reference describes all available settings for cases where a default is not appropriate — for
example, a registry in another account, a different load concurrency, a custom retention period, or
a customer-managed engine role.

Two settings are intentionally absent from the configuration file and cannot be added: whether a
run writes (`--live`) and which records it covers (`--incremental` / `--since`). These are per-run
decisions passed as command-line flags.

**Contents**

- [The configuration file](#the-configuration-file)
- [Engine settings](#engine-settings)
- [Load settings](#load-settings)
- [Transform settings](#transform-settings)
- [Registry pair settings](#registry-pair-settings)
- [IAM settings](#iam-settings)
- [Configuration resolution and validation](#configuration-resolution-and-validation)

## The configuration file

The following four values are required. All other settings have defaults.

```jsonc
{
  "engine": {
    "account": "111122223333",         // AWS account where the migration runs
    "region": "us-west-2"
  },
  "registries": [
    {
      "id": "registry-1",              // Identifies this pair in reports and staging paths
      "source": { "accountId": "111122223333", "region": "us-east-1", "registryId": "<preview-registry-id>" },
      "target": { "accountId": "111122223333", "region": "us-west-2", "registryId": "<new-registry-id>" }
    }
  ]
}
```

To migrate additional registry pairs, add entries to the `registries` array. All configured pairs
are migrated in the same run, and each pair produces its own reports.

The configuration file is resolved in the following order:

1. The path specified by `--config`
2. `./migration.config.json`
3. `./config/migration.json`

## Engine settings

| Setting | Default | Description |
| --- | --- | --- |
| `engine.account`, `engine.region` | Derived from credentials | AWS account and Region where the migration engine runs |
| `engine.stagingDirectory` | `migration-runs` (adjacent to the config file) | Local staging directory used when no S3 bucket is deployed |
| `engine.stagingBucket` | — | S3 bucket for staging. Written by `deploy`. When present, staging uses S3 instead of the local directory |
| `engine.stackName` | `AgentRegistryMigrationEngine` | AWS CloudFormation stack name for the optional AWS Glue engine |
| `engine.deploymentId` | `default` | Identifies a deployment, allowing two migration waves to run concurrently |
| `engine.createIamRoles` | `true` | When `false`, the stack creates no IAM resources and `glueRoleArn` must be provided. See [Permissions](iam.md#the-engines-execution-role) |
| `engine.glueRoleArn` | — | The AWS Glue execution role to use when `createIamRoles` is `false` |
| `engine.accessRoleName` | `AgentRegistryMigrationAccess` | Name of the generated cross-account access role |
| `engine.externalId` | — | External ID required when assuming a cross-account role |
| `engine.glueWorkerType` | `G.1X` | AWS Glue worker size for both jobs: `G.1X`, `G.2X`, `G.4X` or `G.8X`. `G.1X` is the smallest a Glue 5.0 batch job accepts |
| `engine.glueNumberOfWorkers` | `2` | Workers per job. `2` is the AWS Glue minimum, and the jobs are single-threaded, so raising it only adds cost |
| `engine.glueTimeoutMinutes` | `180` | AWS Glue job timeout in minutes |
| `engine.stagingRetentionDays` | `90` | Amazon S3 lifecycle expiration for staged records |
| `engine.reportRetentionDays` | `365` | Amazon S3 lifecycle expiration for reports |
| `engine.terminationProtection` | `true` | Enables CloudFormation termination protection on the stack |
| `engine.autoRunTransformAfterExtract` | `false` | When `true`, chains the Glue jobs so that the transform-load stage starts automatically after extract, skipping the manual review step |
| `engine.partition` | `aws` | AWS partition. Set to `aws-us-gov` or `aws-cn` for AWS GovCloud (US) or China Regions |
| `engine.parameterPrefix` | `/agent-registry-migration/<deploymentId>` | AWS Systems Manager Parameter Store prefix for deployed configuration |

## Load settings

| Setting | Default | Description |
| --- | --- | --- |
| `runtime.load.matchSourceStatus` | `true` | Moves each migrated record to the status it holds in the preview registry. When `false`, all records are left in `DRAFT` status and are not returned by data-plane search or browsing APIs |
| `runtime.load.failOnRecordError` | `false` | Record-level failures are skipped and listed in the report instead of stopping the run. Set to `true` for an all-or-nothing load, which fails the run (nonzero exit, report status `FAILED`) as soon as any record fails |
| `runtime.load.loadConcurrency` | `32` | Number of records loaded in parallel (1–32). Because each record is primarily waiting on network I/O, increasing concurrency reduces total run time without requiring additional compute capacity. Lower it if the target control plane throttles the run |
| `runtime.load.recordsPerObject` | `500` | Number of records per staged S3 object |
| `runtime.load.dumpExtractedRecords` | `true` | When `false`, the human-readable copy of extracted records is not written |
| `runtime.load.mode` | `FULL` | Default scope for a job started outside the CLI. The `run --incremental` flag overrides this per run |
| `runtime.load.changedAfter` | `null` | Default incremental cutoff for jobs started outside the CLI. The `run --since` flag overrides this per run |
| `runtime.load.dryRun` | `true` | Default for jobs started from the AWS Glue console. The `run` and `run --live` commands override this per run |
| `runtime.load.allowReplayConfigurationDrift` | `false` | When `true`, allows a load run to use an extract taken under different transform settings. This setting is disabled by default because it makes a replay non-reproducible |

## Transform settings

| Setting | Default | Description |
| --- | --- | --- |
| `runtime.transform.duplicateNames` | `fail` | Behavior when two preview records share a name. `fail` stops the run and reports the conflicts. `suffix` migrates both records under distinct target names, removing the original preview name from those records |
| `runtime.transform.namePrefix` | `migrated` | Prefix used for fallback names when a source record has no usable name |
| `runtime.transform.allowedRecordTypes` | `AGENT`, `MCP`, `SKILL`, `CUSTOM` | Restricts migration to records whose inferred `recordType` is in this list |
| `runtime.transform.passthroughFields` | `description` | Fields copied from the source record to the target record without transformation |

## Registry pair settings

| Setting | Default | Description |
| --- | --- | --- |
| `registries[].id` | — | Identifier for this registry pair. Used in all reports and staging paths |
| `registries[].source` | — | The preview registry: `accountId`, `region`, and `registryId` |
| `registries[].target` | — | The target registry: `accountId`, `region`, and `registryId` |
| `registries[].source/target.roleArn` | Derived when the stack creates roles | IAM role to assume for a registry in another account |
| `registries[].source/target.externalId` | Value of `engine.externalId` | External ID required by the role's trust policy |

Each side of a registry pair is configured independently. A pair can span AWS Regions, AWS accounts,
or both. The standard migration path places the target registry in the same account and Region as the
preview registry, but this is not required.

## IAM settings

Both action lists are configuration values, so IAM policies can be scoped without modifying code.

| Setting | Default | Description |
| --- | --- | --- |
| `iam.previewReadActions` | Read-only action list | Actions the migration is permitted to perform in the `bedrock-agentcore` namespace |
| `iam.targetWriteActions` | List and write action list | Actions the migration is permitted to perform in the `agent-registry` namespace |
| `iam.allowUnscopedRegistryResources` | `false` | When `true`, allows `Resource: "*"` in generated policies when a registry ID is not yet known |

For full policy details and action descriptions, see [Permissions](iam.md).

## Configuration resolution and validation

**Local runs.** The configuration file is the sole source. Every command reads it directly from
disk.

**Glue engine runs.** The `deploy` command publishes the configuration file to AWS Systems Manager
Parameter Store. The Glue jobs read configuration from Parameter Store at runtime. As a result:

- A configuration change requires a `deploy` before it affects a Glue run.
- The `check --glue` command validates the deployed configuration in Parameter Store, not the
  local file on disk. This reflects what the jobs will actually read.

Configuration values are validated when they are read and again at `deploy` time. Validation
errors identify the specific field, so a misconfiguration is caught immediately rather than
part-way through a run.
