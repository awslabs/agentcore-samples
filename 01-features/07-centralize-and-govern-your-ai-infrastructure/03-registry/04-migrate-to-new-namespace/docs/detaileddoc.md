## Contents

- [What the migration changes](#what-the-migration-changes)
- [Commands reference](#commands-reference)
- [Complete migration walkthrough](#complete-migration-walkthrough)
- [Migration reports](#migration-reports)
- [Re-running a migration](#re-running-a-migration)
- [Incremental runs](#incremental-runs)
- [Registries in other accounts and regions](#registries-in-other-accounts-and-regions)
- [Troubleshooting](#troubleshooting)

---

## What the migration changes

The migration tool handles all schema transformations automatically. You are not required to edit records manually.

| Preview | New version |
| --- | --- |
| `descriptors` nested under a protocol layer (`a2a`, `mcp`, `agentSkills`, `custom`) | One granular primary key (`a2aAgentCard`, `mcpServer`, `agentSkillsDefinition`, `custom`), with supplementary descriptors (`tools`, `skillMd`) moved under the primary's `additionalData` |
| `inlineContent` | `data` |
| `schemaVersion` / `protocolVersion` | `dataSchemaVersion` |
| One top-level `synchronizationConfiguration` | `source` on each descriptor that supports URL sync |
| `name` (display label) | `displayName` |
| — | `name` — **required**, unique per registry. Carried over from the source record unchanged |
| — | `recordType` — **required**. Inferred from the preview descriptor shape |
| `bedrock-agentcore` endpoints, ARNs, and IAM actions | `agent-registry` |

Descriptor content is never rewritten — only re-keyed. The record comparison report shows both the preview and new versions of every record so you can confirm the transformation.

**Items not handled by this tool** — the following must be updated separately: your IAM policies, service endpoints, SDK clients, CLI commands, resource ARNs, and observability integrations. For the complete list of affected surfaces, see the [AWS migration guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-faq.html).

---

## Commands reference

### Available commands

| Command | Description |
| --- | --- |
| `init` | Prompts for registry information and writes the configuration file. Derives each target registry's configuration automatically, and creates the registry for you once you confirm. |
| `check` | Validates the configuration and verifies that every registry and the staging location is reachable. Exits with a non-zero code on any failure, making it suitable for use as a pipeline gate. |
| `extract` | Reads all records from the preview registries into staging and prints the run ID. Does not write to the target registry. There is no `--live` flag for this command. |
| `load` | Transforms and loads a staged extract. Defaults to the most recent extract; use `--run-id` to specify another. Does not write without `--live`. |
| `run` | Runs both phases in a single command: check, extract, transform, load, and report. Does not write without `--live`. |
| `report` | Displays the outcome of a run and the paths to its output files. Defaults to the most recent run. |
| `deploy` / `destroy` | Optional. Deploys or removes the AWS Glue migration engine. See [Managed migration with AWS Glue](../README.md#managed-migration-with-aws-glue). |
| `target-config` | Analyses a preview registry and derives the configuration its target registry should be created with, translated to the new schema. With `--create`, it also creates each registry, waits for it to become `READY`, and writes the generated ID into the configuration. `init` performs both steps during setup; run `target-config` directly when adding a mapping later. |

### Available options

| Option | Applies to | Description |
| --- | --- | --- |
| `--config <path>` | All commands | Path to the configuration file. Default: `./migration.config.json`, then `./config/migration.json`. |
| `--live` | `run`, `load`, `check` | Creates records in the target registry. Disabled by default. When passed to `check`, reports the run that is about to execute, including confirmation that it will write. |
| `--dry-run` | `run`, `load`, `check` | Transforms and reports without writing. This is the default behavior; the flag is available for explicit declaration. |
| `--run-id <id>` | `load`, `report` | Specifies a run by ID. Default: the most recent run. |
| `--resume [run-id]` | `run` | Loads a previously reviewed extract instead of performing a new extraction. With no ID specified, uses the most recent extract. |
| `--incremental` | `run`, `extract`, `check` | Processes only records whose source `updatedAt` timestamp is at or after the last successful load watermark. |
| `--since <when>` | `run`, `extract`, `check` | Processes records changed after an explicit ISO-8601 timestamp. Implies `--incremental`. |
| `--glue` | `run`, `extract`, `load`, `check`, `report` | Uses the deployed AWS Glue engine instead of running locally. `check --glue` validates the deployed configuration that the jobs will read. |
| `--local` | `run`, `extract`, `load`, `check`, `report` | Stages output in a local directory even when a bucket is deployed. Does not use AWS infrastructure. |
| `--json` | `check`, `report` | Produces machine-readable output. |
| `--offline` | `check` | Validates configuration only; does not make AWS API calls. |
| `--yes` | `deploy`, `destroy` | Skips the AWS CDK approval prompt, or confirms deletion. |
| `--delete-data`, `--keep-reports` | `destroy` | Controls what happens to the staging bucket on teardown. |
| `--force` | `init` | Overwrites an existing configuration file. |
| `--create` | `target-config` | Creates each derived target registry, waits for it to become `READY`, and writes the generated ID into the configuration. Without it, `target-config` only derives and prints the settings. |

Options are validated against the command they are passed to. Specifying an option that does not apply to a given command causes the tool to exit with an error, because each option represents a per-run decision and silently ignoring one could cause a run to behave differently from what was intended. Use `agent-registry-migration <command> --help` (or `help <command>`) to see the options available for a specific command.

**Exit codes:** `0` — success; `1` — the command ran and failed; `2` — the command could not be invoked (unknown command, invalid option, or no command specified).

---

## Complete migration walkthrough

The following procedure describes a complete first migration. Steps 1, 2, 4, and 6 are required. Steps 3 and 5 are optional but strongly recommended.

| Step | Action | Command | Required |
| --- | --- | --- | --- |
| 1 | Write the configuration | `init` | **Required** — once |
| 2 | Create the target registry | `init` creates it from settings derived from the preview registry, or `target-config --create` later | **Required** — once per registry |
| 3 | Validate access | `check` | Optional, strongly recommended |
| 4 | Extract from the preview registries | `extract` | **Required** |
| 5 | Review what would be written | `load --dry-run` | Optional, strongly recommended |
| 6 | Create the target records | `load --live` | **Required** |

Steps 4 through 6 can be combined into a single `run --live` command if an intermediate review is not required.

### Step 1: Write the configuration (`init`) — Required

Run `init` to configure the migration. The command prompts for each registry pair once and writes the file that all subsequent commands read. Your AWS account and Region are derived from your credentials; most prompts can be accepted with the default value.

```bash
$ agent-registry-migration init

Setting up your migration. This is the only time you are asked for any of this.

Using credentials for arn:aws:sts::111122223333:assumed-role/Admin/you

AWS account id holding your registries [111122223333]:
Registry pair 1
  Account of the Preview registry [111122223333]:
  Region of the Preview registry [us-east-1]:
  Preview registry id: Sl81LNGuAmntzOLC
  Account for the target registry [111122223333]:
  Region for the target registry [us-east-1]:
  Target registry id (leave empty and I will create the new-version registry for you):

Migrate another registry? (y/n) [n]:

Wrote /home/you/migration.config.json
```

Each side of the registry pair is prompted separately because each can be in a different account or Region. The most common case — where the target registry is in the same account and Region as its preview registry — requires only pressing Enter at each prompt.

If you specify a different Region, no additional configuration is required. If you specify a different account, the migration must assume a role in that account. The `init` command explains this requirement and describes the two available options. For details, see [Registries in other accounts and regions](#registries-in-other-accounts-and-regions).

### Step 2: Create the target registry — Required (unless it already exists)

If you left the target registry ID empty, `init` creates the registry for you. It reads the preview registry, translates its authorizer and approval settings into the new schema, writes them to a payload file, shows you that file, and then — with your confirmation — creates the registry, waits for it to become `READY`, and records the generated ID in your configuration.

A registry's `discoveryConfiguration` determines who can read it, which is why the derived payload is displayed before anything is created rather than after.

```bash
For registry-1, these are the target registry's settings, translated from your Preview
  registry:
  /home/you/new-registry-payloads/registry-1.json

Create it now? (y/n) [y]:

Creating the target registry. Each one provisions a workload identity, so this
takes a moment.
  registry-1: dnrwr3bpZ5w0i7Ps  (READY)

Next: agent-registry-migration check
```

The generated ID is written to your configuration file as `target.registryId`, so nothing needs to be copied by hand.

Answer `n` to create the registry yourself instead. The command that applies the same payload is printed, and the prompt for the resulting ID remains available:

```bash
To create registry-1 yourself:
  aws agent-registry-control create-registry --cli-input-json file:///home/you/new-registry-payloads/registry-1.json --endpoint-url https://agent-registry-control.us-east-1.api.aws --query registryArn --output text
  (creation is asynchronous; wait for the registry status to reach READY before loading)

  Target registry id for registry-1 (empty to add it later): dnrwr3bpZ5w0i7Ps
```

To create a registry outside the `init` wizard — when you add a mapping later, or from a script with no terminal to confirm at — use `target-config --create`, which performs the same derive, create, wait, and record steps without prompting:

```bash
agent-registry-migration target-config --create --mapping registry-2
```

> **Note**
> Creating a registry requires `agent-registry:CreateRegistry` and `agent-registry:GetRegistry`, plus three `bedrock-agentcore` workload-identity permissions that are easy to overlook. A missing workload-identity permission surfaces as a `CREATE_FAILED` registry whose `statusReason` is `Unable to create workload identity because access was denied`. For details, see [Permissions — Creating the target registries](iam.md#creating-target-registries).

> **Note**
> If a model in `~/.aws/models/agent-registry-control` overrides the one shipped with the AWS SDK, and that model predates the registry operations, creation fails while record migration continues to work. The `check` command reports this as a warning and names the directory.

### Step 3: Validate access (`check`) — Optional, strongly recommended

Every command performs access validation internally, so running `check` first is not strictly required. However, doing so allows you to detect issues such as an incorrect registry ID, a missing permission, or an unassumable role within seconds, rather than partway through a registry extraction.

```bash
$ agent-registry-migration check
[PASS] config.loadMode: FULL (changedAfter=unset)
[PASS] config.dryRun: dryRun=true: transform/load will NOT write to any target registry
[PASS] registries.registry-1.shape: 111122223333/us-east-1/Sl81LNGuAmntzOLC -> 111122223333/us-east-1/dnrwr3bpZ5w0i7Ps
[PASS] staging.writable: /home/you/migration-runs accepts writes
[PASS] staging.readable: /home/you/migration-runs is readable
[PASS] source.registry-1.reachable: Preview registry 111122223333/us-east-1/Sl81LNGuAmntzOLC is reachable
[PASS] target.registry-1.reachable: target registry 111122223333/us-east-1/dnrwr3bpZ5w0i7Ps is reachable

Pre-flight validation PASSED
```

The `reachable` check assumes the configured role and calls the registry directly. It does not merely validate that the ID is syntactically correct. The command exits with a non-zero code on any `[FAIL]`, making it suitable for use as a pipeline gate.

To preview the settings for a live run before executing it, pass `--live`:

```bash
$ agent-registry-migration check --live
...
[WARN] config.dryRun: dryRun=false: transform/load WILL write to the target registries
         fix: Drop --live to transform and report without writing anything
```

### Step 4: Extract from the preview registries (`extract`) — Required

The `extract` command reads every record from the preview registries into staging and prints the run ID. This command does not write to the target registry and has no `--live` flag.

For large registries, the tool logs progress every 100 records.

```bash
$ agent-registry-migration extract
Reading the Preview registries. Nothing is written to the target registry.
  configuration : /home/you/migration.config.json
  registries    : 1
  staging       : /home/you/migration-runs
  covering      : every record (full)
  run id        : 20260806T143000Z-a1b2c3d4

INFO Starting extract run 20260806T143000Z-a1b2c3d4 for 1 registry mappings
INFO Mapping registry-1: FULL load: every source record is extracted
INFO Extracted 5 records for mapping registry-1 into 1 objects
INFO Extract run 20260806T143000Z-a1b2c3d4 completed with 5 records

Run 20260806T143000Z-a1b2c3d4

Extract: SUCCEEDED -- 5 record(s) from 1 registry mapping(s)
  registry-1: 5 record(s) (CUSTOM=5)

Load: not run yet for this extract

Extracted, and nothing has been written to the target registry. This run id is 20260806T143000Z-a1b2c3d4

Review it, then load it:
  agent-registry-migration load --dry-run   # transform and report, still writing nothing
  agent-registry-migration load --live      # create the target records

Both default to this extract, the most recent one. Pass --run-id 20260806T143000Z-a1b2c3d4 to be
explicit, or name an older run id to load that one instead.
```

### Step 5: Review the transformation (`load --dry-run`) — Optional, strongly recommended

The `load --dry-run` command transforms every staged record to the new schema and validates it against the new Registry API contract, without calling any target write APIs. A `dryRun` count equal to the extracted record count indicates that all records transformed successfully.

```bash
$ agent-registry-migration load --dry-run
Dry run -- nothing will be written to the target registry
  configuration : /home/you/migration.config.json
  registries    : 1
  staging       : /home/you/migration-runs
  run id        : 20260806T143000Z-a1b2c3d4 (the most recent extract)

Transforming and reporting...

Run 20260806T143000Z-a1b2c3d4

Extract: SUCCEEDED -- 5 record(s) from 1 registry mapping(s)
  registry-1: 5 record(s) (CUSTOM=5)

Load: SUCCEEDED -- DRY RUN (nothing written to the target registry)
  registry-1: created=0 updated=0 unchanged=0 dryRun=5 failed=0

Report: /home/you/migration-runs/reports/run_id=20260806T143000Z-a1b2c3d4/attempt=7b41c0de-9f3a-4d55-9a41-2c6e8f0b91aa/summary.html

Nothing was written to the target registry. When the report above looks right:
  agent-registry-migration load --live --run-id 20260806T143000Z-a1b2c3d4
```

Review `summary.html` to verify all checks pass with the current run's values. You can also inspect the record comparison files to see the exact payload that would be sent for each record.

### Step 6: Create the target records (`load --live`) — Required

The `load --live` command writes the same staged data reviewed in the dry run. The content written to the target registry is identical to what the dry run reported. This is the only step in the tool that writes to the target registry.

```bash
$ agent-registry-migration load --live
LIVE -- records will be created in the target registries
  configuration : /home/you/migration.config.json
  registries    : 1
  staging       : /home/you/migration-runs
  run id        : 20260806T143000Z-a1b2c3d4 (the most recent extract)

Creating the target records...

Run 20260806T143000Z-a1b2c3d4

Extract: SUCCEEDED -- 5 record(s) from 1 registry mapping(s)
  registry-1: 5 record(s) (CUSTOM=5)

Load: SUCCEEDED -- LIVE, attempt 2 of 2
  registry-1: created=5 updated=0 unchanged=0 dryRun=0 failed=0
```

A `created` count equal to the extracted record count indicates a complete first migration. `attempt 2 of 2` includes the dry run as the first attempt against this extract.

Records are created in the target registry in the same approval status they hold in the preview registry. An approved record is approved upon creation and is immediately returned by data-plane search and browse APIs. The `approval` block in the report serves as the confirmation:

```jsonc
"approval": {
  "matchSourceStatus": true,
  "sourceStatusCounts": { "DEPRECATED": 1, "APPROVED": 2, "DRAFT": 2 },
  "targetStatusCounts":     { "DEPRECATED": 1, "APPROVED": 2, "DRAFT": 2 },
  "statusesApplied": 3,
  "statusesNotApplied": 0,
  "recordsNeedingResubmission": 0,
  "note": "3 record(s) were moved to the status they hold in the Preview registry; the rest were DRAFT at source and needed no change."
}
```

> **Important**
> Every migrated record receives a new `recordId` in the target registry. Retain the ID crosswalk file. It maps each old `recordId` to its new value and is required to update any references in your systems.

```csv
oldRecordId,newRecordId,previewName,name,displayName,recordType,recordVersion,action,status,targetStatus
i4IVzeKpm2YD,gNz5OlIkKeb0,solo-deprecated,solo-deprecated,solo-deprecated,CUSTOM,,created,SUCCEEDED,DEPRECATED
mcJjICOUb0nN,2ky2X3eyL8LP,payments-mcp,payments-mcp,payments-mcp,CUSTOM,,created,SUCCEEDED,APPROVED
is93g8PdDE93,bNXHw0OtubBd,solo-approved,solo-approved,solo-approved,CUSTOM,,created,SUCCEEDED,APPROVED
SktDArlPH6Sm,W6gmZmNUXCDE,solo-draft,solo-draft,solo-draft,CUSTOM,,created,SUCCEEDED,DRAFT
p94A8RWP4Mz7,6fbyGSs6tqms,search-agent,search-agent,search-agent,CUSTOM,,created,SUCCEEDED,DRAFT
```

`previewName` and `name` are identical unless you opted into renaming colliding records using the `duplicateNames` setting.

If `statusesNotApplied` is non-zero, the per-record `statusError` field in the comparison report identifies the cause. Records that require manual status completion can be submitted with the following command:

```bash
aws agent-registry-control submit-registry-record-for-approval \
  --registry-id <new-registry-id> --record-id <new-record-id> \
  --endpoint-url https://agent-registry-control.<region>.api.aws
```

### Optional: Review a run (`report`)

The `report` command prints the outcome of a run and the paths to its output files, defaulting to the most recent run. It also generates `summary.html` for any attempt that does not already have one.

```bash
$ agent-registry-migration report
$ agent-registry-migration report --run-id 20260801T090000Z-9f8e7d6c   # a specific run
$ agent-registry-migration report --json                               # machine-readable output
```

---

## Migration reports

All output is written to `reports/run_id=<run-id>/` within the staging location.

```
reports/run_id=<run-id>/
  extract-summary.json                              what extraction read
  extracted-records/mapping=<id>/part-*.json        every preview record, as extracted
  attempt=<attempt-id>/
    summary.html                                    the report as a page: every check, answered against this run
    summary.json                                    the same data, in machine-readable format
    id-crosswalk/mapping=<id>.csv                   old recordId to new recordId mapping
    record-comparison/mapping=<id>/part-*.json      per record: preview, transformed, and new versions
    failures/mapping=<id>.json                      present only when records failed
```

The `report` command prints the summary and artifact paths, and generates `summary.html` for any attempt that does not have one — including runs that predate the report page feature.

| Artifact | Description |
| --- | --- |
| `summary.html` | **Start here.** A complete view of the run: all checks answered against this run's data, read-side results (registry warnings and name collisions are visible here, not only on the load side), record counts, approval status, failures, and artifact paths. |
| `extract-summary.json` | Totals, per-registry record counts, record-type and source-status distributions, duplicate record names, warnings, and the time window covered by an incremental run. |
| `summary.json` | Per-attempt results: created, updated, unchanged, and failed counts; the approval block; the replay fingerprint; and paths to all artifacts. |
| `id-crosswalk/mapping=<id>.csv` | **Retain this file.** One row per source record: `oldRecordId`, `newRecordId`, `previewName`, `name`, `displayName`, `recordType`, `recordVersion`, `action`, `status`, `targetStatus`. |
| `record-comparison/…` | Per-record detail: the preview record as extracted, the exact payload sent to the target registry, and the target record as returned by the service. |
| `failures/…` | Failed records with the service error message, the staged source object, and a traceback. |

The five load counters summarize the outcome of each attempt: `created` (new target record), `updated` (existing record with changes), `unchanged` (existing record already matching — no API call made), `dryRun` (transformation validated, nothing written), `failed`. On a successful first live load, `created` equals the extracted record count.

A crosswalk row with an empty `newRecordId` indicates that nothing was written for that record. A **failed** row with a `newRecordId` indicates that the record was created but failed to settle — it exists in the target registry, and the ID can be used to locate it.

---

## Re-running a migration

Running a load against the same records more than once is safe and is the standard approach to recover from a partial failure. Before writing, the tool looks up each record in the target registry using the following order of precedence:

1. **The target record ID from a previous run.** Each live load stores the old-to-new record ID mapping as engine state in `state/idmap/mapping=<id>.json`, adjacent to the watermark. This is checked first because it is the only identifier that remains valid if the source record is renamed in the preview registry.
2. **`name` (and `recordVersion`)**, which is the target deduplication key.
3. For records synchronized from a URL, the **descriptor source URL**, because the service rewrites the record name with the value from the fetched document.

Example outcomes across two loads:

```text
first live load    created: 30   updated: 0   unchanged:  0   failed: 3
second live load   created:  0   updated: 0   unchanged: 30   failed: 3
```

| Situation | Outcome on the second load |
| --- | --- |
| Record already exists and is identical | `unchanged` — no API call is made |
| Record already exists, source has since changed | `updated` — patched in place, same `recordId` |
| Record renamed in preview since the last load | `updated` — the previously migrated record is renamed in place, retaining its `recordId`. The record is not migrated a second time under the new name. |
| Record in the ID map but deleted from the target | Matched by `name` instead, or created again if no match is found. The crosswalk row carries a warning. |
| Record failed on the first load, nothing created | Retried exactly as before. |
| Record created but settled in `CREATE_FAILED` | Refused again. The crosswalk row identifies the broken record. Delete or fix it before re-running. |
| Two source records share one name | Nothing is loaded for that registry. Extraction stops and reports the collision, so subsequent runs behave identically until the source is corrected. With `duplicateNames = "suffix"`, each record retains the distinct name assigned on the first load, because the suffix is derived from the source record's identity. |

The ID map is written even when part of a run fails, and even for records that were created but failed to settle. Unlike the watermark, which advances only after a fully successful load, the ID map is written immediately. This is intentional: every ID in the map corresponds to a record that already exists in the target. If an ID were omitted, the next run would not re-read it safely — it would create a duplicate.

> **Note**
> Dry runs never write to the ID map, because they create no records.

To ensure that a load uses the exact bytes you reviewed in the dry run, pass `--resume` with the same run ID. A `deploy` between extract and load forces a re-extract, because the replay fingerprint covers the transform and target adapter settings, and a deployment updates them. Records are never loaded under different logic than the one used during staging.

---

## Incremental runs

The `--incremental` flag processes only records whose source `updatedAt` timestamp is at or after a cutoff. You do not need to supply a cutoff explicitly: the tool maintains a watermark per registry pair and uses it automatically.

```bash
agent-registry-migration run --incremental              # dry run: shows what would be migrated
agent-registry-migration run --incremental --live       # migrates only records changed since the last load
agent-registry-migration run --since 2026-08-01T00:00:00Z --live   # migrates from an explicit cutoff
```

`--since` implies `--incremental`; do not pass both flags together. Like `--live`, these are per-run options and are not stored in the configuration file. To verify that the watermark exists before running, use `check --incremental`.

**Watermark behavior:**

- The watermark records the timestamp of the last **successful** load, not the last extract. A failed load leaves the watermark unchanged, so the next run re-reads the affected records rather than skipping them.
- Cutoff precedence: an explicit value from `--since` or `changedAfter` takes priority; otherwise the saved watermark is used, minus a five-minute overlap to capture records updated during the previous run window. Re-processing an already-migrated record is harmless because the load performs an upsert.
- That overlap is visible in the counts: for a registry that was active shortly before the last load, or populated in one burst, an incremental extract can legitimately report the same record count as a full one. `changedAfterReason` in `extract-summary.json` names the cutoff it used and where it came from, which distinguishes this from a lost watermark.
- A partial failure or a dry run does not advance the watermark.
- A record edited in the preview registry after migration is picked up on the next incremental run and updated in place, retaining its target `recordId`. Editing a record returns it to `DRAFT` status at the source; the service does the same on update. When the owner re-approves the record in preview, the next incremental run re-approves it in the target registry, even if the content did not change.

Run a full load at least once before using `--incremental`. An incremental run with no watermark and no `--since` value fails the `check` step with an explanatory message rather than silently processing all records.

**Typical cutover sequence:**
1. `extract` → `load --dry-run` → `load --live` (establishes the watermark)
2. Continue serving from the preview registry
3. At cutover: `run --incremental --live` to migrate any records changed since step 1

The `--incremental` and `--since` flags work identically with the AWS Glue engine. `run --glue --incremental --live` passes the scope to the Glue jobs, so the cutover catch-up is independent of where the run executes.

---

## Registries in other accounts and regions

The migration tool supports cross-account and cross-region scenarios. The `registries` configuration is a list, and each side of each registry pair carries its own `accountId`, `region`, and optional `roleArn` and `externalId`. A single configuration file can migrate registries across an entire AWS estate.

When running locally, the tool uses your local credentials and assumes the `roleArn` specified in each mapping. When running on AWS Glue, the Glue execution role is used in the same way. The `check` command assumes every configured role and calls the registry behind it, so a missing role, a mismatched external ID, or an incorrect registry ID is detected in seconds rather than partway through a run.

To migrate a registry in a different account, you need a role in that account. The `deploy` command can provision a `RegistryAccess-<account-id>` CloudFormation stack in the target account for you. Alternatively, you can create the role yourself and reference it in the mapping. Both options, including the required IAM policies, are documented in [Permissions — Registries in another account](iam.md#registries-in-another-account).

---

## Troubleshooting

Every failure message includes guidance on the recommended corrective action. The following table describes the most common issues.

| Message or symptom | Cause | Resolution |
| --- | --- | --- |
| `This tool needs Python 3.10+ with boto3 1.43.66 or newer` | The CLI could not locate a Python interpreter whose boto3 models both registry APIs. The new Registry control plane's model first shipped in botocore 1.43.66, which requires Python 3.10. | Run `python3 -m pip install 'boto3>=1.43.66'`, or set the `PYTHON` environment variable to the path of a supported interpreter. |
| `this SDK has no service model for agent-registry-control` | A pre-flight check found an SDK too old to write target records. On AWS Glue the SDK is installed by the job's `--additional-python-modules`, so this means the deployment predates that setting. | Locally, upgrade boto3 as above. On AWS Glue, redeploy with `agent-registry-migration deploy`. |
| `No configuration at …` | No configuration file was found at the expected path. | Run `agent-registry-migration init`, or pass `--config <path>` to specify the file location. |
| `still has a placeholder target registry id` | The `init` command could not create the target registry, or was answered `n` when it offered to. | Run `agent-registry-migration target-config --create` to create it and record its ID, or create the registry yourself and update `target.registryId` in the configuration file. |
| `Pre-flight validation failed` | A configuration or access check failed. No records were read. | Resolve each `[FAIL]` item and re-run. |
| `AccessDeniedException` reading preview records | The `bedrock-agentcore` IAM policy on the source registry is missing required permissions, or the assume-role path is incorrect. | Verify permissions for both namespaces. The preview registry uses `bedrock-agentcore`; the target registry uses `agent-registry`. See [Permissions](iam.md). |
| `AccessDeniedException` writing target records | The `agent-registry` IAM policy on the target registry is missing required permissions, or the account is not allowlisted for the target endpoint. | Verify the policy, then confirm allowlisting with the service team. |
| `N record name(s) are used by more than one source record` | The preview registry permitted duplicate record names. The new version requires unique names, and migrated records retain their preview names, so loading both would cause one to overwrite the other. No records were extracted or loaded for that registry. | The error and the `registries[].duplicateNames` field in the extract summary list each colliding name and the record IDs involved. In the preview registry, rename all but one record in each set or assign distinct `recordVersion` values, then re-run. If the source cannot be modified, set `runtime.transform.duplicateNames` to `"suffix"` — the error message provides the exact configuration to add. |
| Extract succeeded but warns `... will be migrated under a DIFFERENT name` | `duplicateNames = "suffix"` is set, and colliding records were automatically renamed. | This is expected behavior when that setting is enabled. The crosswalk maps `previewName` to `name`; `displayName` retains the preview name. |
| `approval.statusesNotApplied` is non-zero | A record could not be transitioned to its preview status — either the transition was refused or the source status (such as `CREATE_FAILED`) cannot be applied to a new record. | The record is created and otherwise correct; only its status differs. The `statusError` field in the per-record comparison report identifies the cause. Use `submit-registry-record-for-approval` to complete the approval for eligible records. |
| `Replay configuration validation failed` | The transform or target adapter settings changed after the extract was taken, typically due to a `deploy`. | Re-run without `--resume` so records are re-extracted under the current configuration. |
| `Immutable … key already exists` | The run ID was already used. Run IDs are single-use. | Re-run without `--resume` to start a new run. |
| `Extract manifest for run … is not successful` | The extraction failed or completed only partially. | Review `extract-summary.json` to identify the cause, resolve it, and re-run. |
| `Staged object reconciliation failed` | The staged data no longer matches the manifest. | Re-extract. Do not force the load. |
| `Existing target record <id> is in failure status CREATE_FAILED` | A previous load created the record and it never settled — typically because a `source.fromUrl` URL could not be fetched by the service. The tool refuses to overwrite the broken record on re-run. | Fix the URL in the source record, or delete the broken target record, then re-run the load. |
| `Exactly one valid descriptor is allowed for record type SKILL` | A `SKILL` record was sent to the target registry with a descriptor other than `agentSkillsDefinition` or `custom`. | Markdown-only skills are migrated as `agentSkillsDefinition` with the Markdown content under `additionalData.skillMd`. If this error still occurs, the record contains an unexpected skill descriptor. Inspect it using the record comparison report. |
| `Unable to create workload identity because access was denied` from `init` or `target-config --create` | Target registry creation provisions an AgentCore Identity workload identity in the `bedrock-agentcore` namespace. | Add the three workload-identity IAM actions listed in [Permissions — Creating the target registries](iam.md#creating-target-registries). These permissions are needed only to create a registry, not to migrate records. The registry that reported this is left in `CREATE_FAILED`; its ID is printed, so it can be deleted before retrying. |
| `Invalid choice: 'agent-registry-control'` | The AWS CLI in this shell predates the registry operations. Only affects the command printed for creating a registry yourself. | Use `agent-registry-migration target-config --create`, which makes the same call through this tool's own pinned SDK, or update the AWS CLI. |
| `overrides the SDK's own agent-registry-control model` (a `check` warning naming a directory under `~/.aws/models`) | A model file in your home directory takes precedence over the one shipped with the SDK. If it predates the registry operations, creating a registry fails. | Delete that directory, unless you installed it deliberately. Record migration is unaffected either way. |
| `No deployed engine found for stack …` | The `--glue` flag was passed but the Glue engine has not been deployed. | Run `agent-registry-migration deploy`, or remove the `--glue` flag to run locally. |
| `No extract that is ready to load was found` | `load` or `--resume` was invoked but no staged extract is available. | Run `agent-registry-migration extract` first. The command prints the run ID that `load` will use by default. |
| A few records fail but the rest succeed | `failOnRecordError = false` causes the tool to log failures and continue. | Review `failures/mapping=<id>.json` for per-record errors and tracebacks. Fix the cause and re-run. Records that succeeded are detected as unchanged on re-run and are not duplicated. |

**Where to look when diagnosing an issue:** Start with `summary.html`. If more detail is needed, run `agent-registry-migration report`, then inspect `failures/mapping=<id>.json` for per-record errors and tracebacks. For AWS Glue runs, check the Glue job logs in Amazon CloudWatch for job-level issues.

---

## Infrastructure cost for managed migration with AWS Glue

The deployed stack creates an S3 bucket, two Glue jobs, and an SSM Parameter Store standard parameter. You are only charged for what runs.

| Resource | Pricing basis | Estimated cost (30 min per job, both jobs) |
| --- | --- | --- |
| AWS Glue job (extract) | $0.44 per DPU-hour | 2 DPU × 0.5 hr × $0.44 = **$0.44** |
| AWS Glue job (load) | $0.44 per DPU-hour | same as extract |
| S3 staging bucket | $0.023 per GB-month | a few MB of JSONL — **< $0.01** |
| SSM Parameter Store (standard) | Free | $0.00 |
| **Total** | | **< $0.90** |

30 minutes per job is a conservative upper bound — most migrations complete well under that, and billing is per second with a one-minute minimum.

Both jobs run on AWS Glue 5.0 with the default `engine.glueWorkerType` of `G.1X` and `engine.glueNumberOfWorkers` of `2`, which is the smallest configuration AWS Glue accepts for a batch job — 2 DPU. Raising either only costs more: each job is a single-threaded loop that uses one worker and never distributes work, so a wider or larger cluster does nothing for a migration.

Pricing shown is for `us-east-1`. Rates may vary by region. See [AWS Glue pricing](https://aws.amazon.com/glue/pricing/) for current rates.
