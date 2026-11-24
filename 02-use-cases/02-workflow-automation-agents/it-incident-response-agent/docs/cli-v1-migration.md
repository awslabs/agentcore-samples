# CLI-v1 fresh deployment and lifecycle boundaries

Despite this historical filename, this is **not an in-place migration guide**.
The sample carries forward its custom infrastructure and workflow to CLI 1.2.0.
`migratedFrom: "v1"` preserves some physical naming conventions; it does not map
old construct logical IDs. Existing v0 stacks must not use this entrypoint.

## Fresh-only operation

`scripts/deploy.sh` delegates to `lifecycle.py`. It requires one explicit project,
target, account and region, matching exactly one `aws-targets.json` entry. It:

1. Rejects any existing deployment ledger or CLI deployed/migration state,
   including empty state files. Configuration alone is not deployment proof.
2. Checks STS caller account and rejects conflicting ambient region/account/name.
3. Rejects a previously used project stack-name prefix, including another target
   or deleted history, and checks project-qualified OAuth provider collisions. Incomplete listings or permission errors do not prove absence.
4. Records an attempt, then calls **CreateStack**, not UpdateStack, to reserve the
   name. A racing same-name stack causes failure rather than adoption.
5. Records the returned StackId and verifies the new stack and nonce owner tags.
6. Invokes native `agentcore deploy --target ... --skip-bootstrap` against that
   proven reservation, using a private CLI HOME with `transactionSearch: false`.
   It verifies that the real CLI reads that preference. The custom CDK bin
   selects the single target and checks the ledger/owner binding.
7. Records stack resources/outputs, and requires CLI state to contain the same
   StackId before reporting a deployed fixture.

The reservation is an empty SNS topic with no subscribers or publishing. It is
replaced by the full custom stack; it is not a legacy import/refactor and does
not put a WaitConditionHandle's presigned URL into the resource ledger. Direct CLI/CDK deploy bypasses the
sample's operator safeguards and is not a supported entrypoint. Offline preview
uses `IT_SAMPLE_SYNTH_ONLY=true`; preview templates contain a failing CloudFormation
rule so they cannot accidentally be deployed. The fresh wrapper clears preview mode.

## Failures and retries

The helper never erases state. A CLI failure leaves the reservation/partial stack
and any credentials visible for cleanup. A CreateStack timeout before its ARN is
recorded is **unproven**: do not re-run or guess a stack name. An operator must
investigate the recorded request token and reconcile ownership independently.

A renamed project, missing deployed CLI state, another account/region, mismatched
StackId, or changed owner tag is rejected. Only a reservation that demonstrably
never dispatched the CLI may lack CLI state. Once CLI dispatch starts, missing
state is unproven and blocks automated cleanup too; investigate partial credential
creation rather than erasing/restoring an invented empty state. There is no general redeploy/update
or legacy-adoption command in this example. Jira secret-only rotation is a narrow
exception using the existing template and one explicit parameter.

## Deletion and retained resources

`destroy.sh` requires the complete StackId as confirmation. It records the actual
resource inventory before DeleteStack, propagates waiter failures, and checks
`DELETE_COMPLETE` on the recorded ARN. Neither CLI state nor the ledger is removed.
The final ledger status is `STACK_DELETED_RETAINED_REVIEW_REQUIRED`.

Review each retained resource with its owner before deletion:

- Tables/buckets/vector data when retention is configured;
- The Jira ownership table (always retained for lifecycle evidence);
- The externally managed Jira secret and its versions (never owned by this stack);
- CLI-managed OAuth providers and their Identity-managed secrets;
- Runtime/evaluation/Lambda logs, ECR images and any retained service artifacts.

Rollback can remove retained resources from the current stack resource list, so
the helper also preserves a bounded CloudFormation event inventory. CDK's Provider
framework ignores Delete following a failed Create; the helper checks the
retained Jira ownership table and actual provider absence after stack deletion.
An orphan, read denial or incomplete inventory still fails cleanup, even if the
CloudFormation waiter succeeded. It never auto-adopts or deletes an unproven orphan.

The Jira handler only deletes a provider whose retained ARN/creation-time proof
matches GetOauth2CredentialProvider. Only not-found is successful absence. An
ambiguous Create without recorded proof is never auto-adopted on retry.

Shared Transaction Search and CDK bootstrap settings are never undone by cleanup.
Account-wide resources are not owned merely because this sample uses them.

## Review gates

Offline schema/stub tests are not IAM simulation, ARM64 execution, deployment,
real IdP/Jira consent, model invocation, evaluator completion or cleanup evidence.
The exact dependency locks remain committed. npm audit still fails, including a
critical development finding and production advisories. These need independent
security review; do not suppress audits or force incompatible upgrades.
