# Development Guide

This guide is intended for contributors modifying the migration tool. It is not required for
performing a registry migration.

**Contents**

- [Repository layout](#repository-layout)
- [Quality gate](#quality-gate)
- [Seeding a registry for testing](#seeding-a-registry-for-testing)
- [Throughput and benchmarking](#throughput-and-benchmarking)
- [Service models come from the SDK](#service-models-come-from-the-sdk)
- [Descriptor validation is this tool's job, not the SDK's](#descriptor-validation-is-this-tools-job-not-the-sdks)

## Repository layout

```
cli/                    CLI entry point, shared context, and command implementations
bin/cdk-app.ts          AWS CDK app entry point for the optional AWS Glue engine
lib/                    CDK constructs: configuration loading and validation, engine stack,
                        cross-account access stack
config/                 Configuration templates (actual configuration files are not committed)
docs/                   Reference documentation
glue/
  extract.py            Stage 1 entry point (AWS Glue execution)
  transform_load.py     Stage 2 entry point (AWS Glue execution)
  common/migration_common/
    __main__.py         Single entry point invoked by the CLI
    jobs/               Stage 1 (extract) and stage 2 (transform-load) implementations
    transform.py        Preview-to-new-version record transformation logic
    registry_api.py     Control-plane clients for both namespaces and the target request contract
    settings.py         Configuration loading, validation, and the --live override
    preflight.py        Checks performed by the `check` command
    report_html.py      HTML run report generation
    storage.py          Amazon S3 staging
    local_store.py      Local filesystem staging
    teardown.py         Resources removed by the `destroy` command
    target_registry.py  Target registry configuration derivation, creation, and READY wait
    adapter/            Shared API contract
  common/tests/         Offline test suite (no AWS calls, no credentials required)
tools/                  Development utilities: wheel and fingerprint verification,
                        load benchmarking, registry seeding
```

## Quality gate

Run the following commands before submitting changes:

```bash
npm run check              # TypeScript compilation, Python compileall, full Python test suite,
                           # and CLI mode assertions
npm test                   # Python test suite only
npm run verify:lib         # Builds the wheel and verifies it contains all modules and the
                           # bundled API contract
npm run verify:fingerprint # Verifies that the replay fingerprint matches across checkout,
                           # package, and stack
npm run synth              # Build and CDK synth
```

The CI pipeline defined in `.gitlab-ci.yml` runs the same checks on every push: the Python test
suite on Python 3.11 (the AWS Glue 5.0 interpreter), followed by a build and CDK synth of both example
configurations. The synth asserts that the customer-managed-role configuration produces no IAM
resources. No AWS credentials are required in the pipeline.

The pipeline also runs `verify:fingerprint`. This check cannot be replaced by a unit test. The
replay fingerprint includes a hash of the runtime Python, computed independently by the checkout,
by an installed package, and by the CDK stack at synth time. All three values must match.
Divergence means that records staged by a local run cannot be live-loaded by the deployed engine.

Because the packaged hash depends on the `files` field in `package.json`, including or excluding a
single `.py` file breaks cross-mode replay while the full test suite continues to pass. For this
reason, the check is performed against a real `npm pack` tarball and a synthesized CloudFormation
template rather than in the offline test suite.

The test suite uses the standard `unittest` framework, requires no AWS credentials, and is
organized by the behavior each file protects:

| Test file | What it protects |
| --- | --- |
| `test_transform.py` | Exact target registry payloads produced by the transform |
| `test_load_guards.py` | Guards between staged data and the target registry |
| `test_engine_entrypoint.py` | That `--live` is the only flag that enables writes |
| `test_report_html.py` | The set of items a run report presents for reviewer action |
| `test_jobs_end_to_end.py` | That the load stage consumes exactly what the extract stage produced, using an in-memory S3 |

## Seeding a registry for testing

Two scripts create preview registries populated with test data, allowing migrations — or changes to
the tool — to be validated against controlled data instead of production registries.

### Full coverage fixture

`tools/seed_preview_test_registry.py` creates a preview registry and populates it with a matrix
covering every descriptor variant and the edge cases the transform must handle:

- Nested supplementary descriptors
- Per-descriptor sync sources
- Versions, Unicode characters, and large payloads
- Boundary-length names
- Target `(name, recordVersion)` deduplication key edge cases: case-only name differences,
  separator-only name differences, and records with no version
- `DEPRECATED` status records
- Boundary values at the service-enforced limit (descriptor content is capped at 100 KB summed
  across all descriptors, not per `inlineContent` as the model implies)
- Records where `updatedAt > createdAt`, which is the condition an incremental run uses to
  identify changed records

All fixtures represent shapes that the live service accepts. A rejection indicates that a fixture
drifted or the service contract changed. The run reports the affected records and exits with code 2.

```bash
python3 tools/seed_preview_test_registry.py --dry-run      # List scenarios without making API calls
python3 tools/seed_preview_test_registry.py                # Create and populate the registry
python3 tools/seed_preview_test_registry.py --profile <profile> --region us-east-1
python3 tools/seed_preview_test_registry.py --registry-id <id>    # Populate an existing registry
```

Two fixture groups require opt-in because they depend on external infrastructure:

- `--a2a-sync-url` requires a reachable agent card endpoint. Without it, the sync-enabled records
  settle in `CREATE_FAILED`.
- `--with-credential-providers` requires an OAuth2 credential provider and an assumable IAM role.

For this reason, only one fixture references a shared MCP sync URL. A successful sync overwrites
the record's name and `recordVersion` from the fetched document on both sides of the migration,
causing two records synced from the same upstream to collapse onto a single deduplication key.

### Parity fixture

`tools/seed_live_parity_fixture.py` creates a small preview registry with two records that share a
name and have no `recordVersion` (a real collision in the new version), plus records in approved, draft, and
deprecated status. It also creates a target registry with auto-approval disabled. This combination
validates duplicate-name handling and approval-status parity end to end.

```bash
python3 tools/seed_live_parity_fixture.py
python3 tools/seed_live_parity_fixture.py --preview-registry-id <id>   # Reuse an existing fixture
```

The preview API validates descriptor content against the A2A, MCP, and skill schemas. Hand-authored
fixtures are frequently rejected. Use the provided scripts rather than constructing payloads
manually.

## Throughput and benchmarking

Loading a record requires one target create call followed by polling until the record settles. The
operation is almost entirely network I/O, which is why increasing `loadConcurrency` reduces run
time — it overlaps waiting periods rather than adding compute.

```bash
npm run benchmark -- --records 96 --latency-ms 50
```

This command runs a simulation: the real load loop executes with a configurable sleep replacing target-service
latency. Treat the result as an upper bound. A live run produces lower throughput because the target
control plane throttles requests and retries consume wall time that the simulation does not model.

The benchmark establishes two properties: throughput scales near-linearly until the service becomes
the bottleneck (not the job), and output is byte-for-byte identical at every concurrency setting
because results are emitted in input order.

## Service models come from the SDK

The migration tool communicates with both control planes through modeled `boto3` operations. Neither
service model lives in this repository: both come from the installed `boto3`/`botocore`, so the SDK
on whatever runs the tool has to carry `bedrock-agentcore-control` (Preview, the source) and
`agent-registry-control` (the new version, the target). Building a client raises `UnknownServiceError` when it
does not. Two consequences worth knowing about:

* Tests that read a model (`test_registry_clients.py`, `test_target_registry.py`) skip rather than
  fail when the SDK has no such model — the same condition the tool itself reports at run time.
* A model under `~/.aws/models/agent-registry-control` takes precedence over the SDK's own, and an
  interim copy left there from before the registry operations shipped makes `CreateRegistry` look
  absent. `agent-registry-migration check` warns about this (`sdk.shadowedTargetModel`) when run on a
  workstation; the Glue jobs never see a home directory, so they never run that check.
* The deployed jobs get their SDK from `--additional-python-modules`, pinned in
  `GLUE_SDK_MODULES` (`lib/migration-engine-stack.ts`), because no AWS Glue image ships an SDK new
  enough to carry the target service model. The pin is exact rather than a floor, so two runs of one
  cutover cannot stage and load with different SDKs. `agent-registry-migration check` reports the
  version each side actually got.

What *is* bundled into the wheel is the API contract:

```
glue/common/migration_common/adapter/api-adapter.json   # Endpoint configuration, field name
                                                        # mappings, and polling rules
```

`api-adapter.json` is shared between local and deployed runs: the CDK app publishes it to AWS
Systems Manager Parameter Store, and a local run reads it directly from the package. This ensures
that a local run and a Glue run use an identical API contract. The contract is part of the replay
fingerprint.

## Descriptor validation is this tool's job, not the SDK's

In the target service model, `descriptors` and `filters` are typed as `Document`, so botocore validates only
top-level members. All descriptor-level validation happens in `validate_target_request`
(`registry_api.py`).

Should a later SDK fully type those shapes, botocore starts rejecting client-side any payload the
real shapes disallow — including payloads that pass today. Run `npm run check` first: the transform
characterization tests in `test_transform.py` pin the exact payloads this tool sends, so shape
disagreements surface there before they surface against the service.

One known disagreement: the API reference lists `agentSkillsMd` as a valid primary descriptor type
for `SKILL` records, but the deployed service rejects it. The transform normalizes those records
onto `agentSkillsDefinition` with the Markdown content under `additionalData.skillMd`. The rejected
shapes and their exact error messages are documented in `test_transform.py`. A model that types
`agentSkillsMd` as a primary descriptor reflects a disagreement between the model and the deployed
service, not permission to send it.

Also unresolved: whether `CreateRegistryRecord` accepts a `metadata` field. If a later service
version does, record the source record ID there — that makes the crosswalk reconstructible from the
registry itself and lets the tool reject updates to records it did not create. The current service
rejects the field.
