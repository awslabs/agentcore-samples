# Pilot validation record

Validated **2026-11-18** with AgentCore CLI **1.0.3**, Python **3.13.11**, and the checked-in
Python/CDK lockfiles. Cloud tests used an explicitly approved sandbox in **us-west-2**.

**Status: local and cloud smoke validation passed; test resources cleaned up.** This is not a
production certification, an exhaustive model evaluation, or a claim that the build toolchain
has no known vulnerabilities. Outstanding toolchain findings are listed below.

## Completed checks

| Check | Result |
|---|---|
| Deterministic tests | **39 tests pass**: real Strands loop with scripted inference, tool behavior, HTTP validation, concurrency/isolation, browser-origin rejection, time/call limits, error redaction, response formatting and IAM/ZIP assertions |
| Real local development | CLI headless server, real Bedrock Nova Lite calls, correct product and return-policy answers; only loopback listening; development processes stopped after tests |
| Real cloud deployment | Two uniquely named test deployments reached READY through CLI v1; final run used the reviewed code and permissions boundary, including an existing KMS-encrypted bootstrap bucket |
| Real cloud invocation | Correct Smart Watch price ($249.99), 30-day electronics return policy and packaging requirement; logs confirm both actual lookup tools ran |
| Stateless behavior | Later requests with the same Runtime session ID did not recover a synthetic marker from an earlier request, both locally and in AWS |
| Read-only behavior | The live model refused requests to issue a refund and read a system file; actual tool exposure is independently asserted to be only the two lookups |
| Invalid input | Empty prompts produced local HTTP 400/CLI exit 1 and cloud Runtime error 400/CLI exit 1, without a model call |
| Inbound authentication | An unsigned request to the deployed Runtime was rejected with HTTP 403 |
| Logs | Fixed tool-completion names observed; synthetic prompt/marker absent from collected application logs; account-level Bedrock invocation logging was checked separately |
| Permissions | Eleven supported IAM simulation cases confirmed the required model, exact ZIP, sample-log writes and S3-mediated KMS access; other models/assets, writes, configuration changes, secret reads, log reads and direct KMS decryption were denied. See the Logs simulation caveat below |
| Packaging | Built ZIP contains current source and Linux AArch64 native dependencies, without `.env`, Git/virtualenv or test payloads |
| Cleanup | Documented CLI remove/deploy sequence deleted each test stack; Runtime, execution role, boundary, sample log group/resource policy, and exact uploaded ZIP/template versions verified absent |
| Shared infrastructure | Existing bootstrap stack ID, update timestamp, outputs and bucket encryption unchanged; no deletion of shared buckets, keys or bootstrap resources |
| Static checks | Ruff lint/format, TypeScript compilation and diff whitespace checks pass; source secret scan found no findings |
| Runtime dependencies | `pip-audit` found no known vulnerabilities in the locked production Python set at review time |

Across both rounds, validation was bounded to **20 live-model sample invocations** total (10 local,
10 cloud), plus health/authentication/invalid-input checks. An invocation can make multiple model
calls. This is a test-volume record, not a measured AWS billing amount.

The unit tests exercise the actual framework and HTTP adapter rather than mocking the entire
application. They complement—not replace—the live model and cloud tests.

## Findings fixed during live validation

1. **Encrypted deployment assets.** The test account's existing bootstrap bucket uses a
   customer-managed KMS key. Added optional, exact-key `kms:Decrypt` permission to both the role
   and boundary, constrained to S3 and the asset/bucket encryption context. No key or bucket policy
   changes were made. Both unencrypted-default synthesis and this live encrypted path are covered.
2. **Nova reasoning markup.** Real responses sometimes included `<thinking>` preambles. The
   application requests customer-facing output, strips complete tagged preambles, and rejects
   malformed or answerless results. Four regression cases cover the observed formatting issue.
3. **Runtime log-delivery setup.** The first boundary denied Runtime's attempt to configure
   delivery on its own log group. Added `logs:PutResourcePolicy` only for the sample log-group
   prefix. The final deployment's CloudTrail event and actual RESOURCE-scoped policy confirm
   successful setup, with the X-Ray service principal constrained by source account/ARN. This is
   not permission to manage account-wide or unrelated log policies. Cleanup verified the policy
   disappeared with the sample log group.
4. **Project-name constraint.** CLI v1 project names have a 23-character limit; documentation now
   calls this out when choosing a unique deployment name.

### IAM simulator caveat

For resource-scoped `logs:PutResourcePolicy`, the simulator reported implicit denial even though
CloudTrail confirmed the service successfully performed the exact scoped operation and the policy
was present. The raw discrepancy was preserved; those two Logs policy simulation cases were not
counted as passing security evidence. For this operation, evidence is the synthesized/live IAM
scope and the actual service result. No write was attempted against an unrelated log group.
IAM simulation generally is not a complete evaluation of every SCP, resource policy or service
behavior; do not promote these checks into a universal access-control guarantee.

## Account-level CLI behavior to review before deployment

CLI 1.0.3 attempts to enable **CloudWatch Transaction Search** during deployment, independently
of the sample's disabled OTel export. The test account already had it enabled; no successful
account-level Transaction Search setup writes were observed in the test audit. The README now
requires checking the existing state and obtaining platform-owner approval before allowing a
change in a fresh account. The sample's resource-scoped log-delivery policy is separate from this
account-level setting.

## Dependency findings that remain open

The **runtime Python dependency audit is clean**, but the **build-tool chain is not**:

- The CLI-generated CDK library `2.269.0` bundled a high-severity `brace-expansion` finding.
  Updating to compatible `aws-cdk-lib 2.277.0` and CDK CLI `2.1148.1` removed that high finding.
  Build, synthesis, actual deployment and cleanup pass with this pair.
- `npm audit` still reports the moderate bundled `minimatch` glob-amplification advisory
  [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249). Three affected-package
  entries describe one dependency chain. An npm override does not replace CDK's bundled module;
  ineffective overrides were removed.
- A fresh installation of published `@aws/agentcore@1.0.3` reports additional high/moderate
  transitive advisories, including glob matching, Zod and telemetry-related dependencies.
  These are local CLI/build dependencies, **not included in the deployed Python ZIP**.
  Affected-path assessment and upstream fixes remain necessary; not using the browser inspector
  is not a general vulnerability fix.
- Do not use `npm audit fix --force` to obtain a clean number: the suggested CLI downgrade is
  incompatible with this v1 migration. Do not suppress these findings or claim “no security issues.”

Use trusted build inputs. Resolve or explicitly disposition these findings with the maintainers
before treating the pilot as ready for broad promotion.

## Limits of this validation

- One sandbox/Region, one model, and a bounded set of questions; not a compatibility matrix,
  load test, penetration test or comprehensive prompt-injection evaluation.
- No end-user authentication layer, persistent memory, real customer data, refund transaction,
  private-network deployment or cross-account access is covered.
- The fixed tool set and IAM boundary reduce impact; they do not guarantee factual answers or
  resistance to every prompt attack.
- Prompt/time/call limits do not replace ingress body limits, account-wide quotas, abuse controls
  or cost budgets. Cancellation does not guarantee a provider stops already-started work.
- SDK Pydantic/datetime deprecation warnings remain recorded rather than suppressed.
