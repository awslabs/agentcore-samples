# VPC egress Gateway — CLI v1

This existing project starts empty: no deployment account, Gateway, Identity
provider or Runtime is configured. CLI/CDK **1.0.3** uses JSON schema **version 2**.
The parent custom infrastructure is separate; see [source provenance](../SOURCE_PROVENANCE.md).

## Local setup and checks

From `vpcegress/`, use a local CLI installation, not a global replacement:

```bash
npm install --prefix .tools --no-save --ignore-scripts @aws/agentcore@1.0.3
export PATH="$PWD/.tools/node_modules/.bin:$PATH"
agentcore --version
npm --prefix agentcore/cdk ci
npm --prefix agentcore/cdk test

python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r ../requirements.txt
pip install --require-hashes -r ../docker/fastmcp-mock/requirements.txt
python -m unittest discover -s tests -v

# Separate parent CDK app; use its checked-in dependency lock.
(cd .. && npm ci && npm test)
```

The control graph is generated from `../requirements.in` and hash-locked in
`../requirements.txt`; it is separate from the two server image locks. Refresh
and validate the graphs deliberately, not by installing unpinned extras.

The local tests cover real SDK models with stubbed service responses, negative
inputs, durable state, server tool calls in-process, and parent/nested synthesis
with **fake context**. They make no AWS calls. Parent synthesis stages/identifies
container assets but **does not build images**. The two external ECS MCP servers
are Python 3.12/Linux AMD64 containers. Build and start both real Docker images
before a cloud test; see [packaging notes](../docker/README.md). EKS retains its
historical inline workloads, not those Docker assets.

The nested project declares no AgentCore Runtimes. The migration also exercised
its actual 1.0.3 CodeZip packager on both existing server sources, producing real
Python 3.13/Linux ARM64 diagnostic artifacts. Those ZIPs are **not** a Runtime
registration, a Docker image, or evidence of ARM64 startup.

## Resource ownership

| Owner | Resources |
| --- | --- |
| Nested CLI project | Gateway and its execution role |
| Parent CDK app | VPCs/NAT, backend APIs, ECS/EKS, DNS zones, certificates, peering |
| `create_target.py` | Private Gateway targets; tracked outside CLI state |
| `create_credential.py` | Outbound Identity providers; tracked outside CLI state |
| `lattice_resources.py` | Self-managed Resource Gateway and Resource Configuration |
| `proxy_backend.py` | Internal ALB proxy, target group, listener/rule, security group |
| `configure_eks_endpoint.py` | Its new private DNS alias/new ingress rule; a dedicated RG group for legacy no-SG NLBs; Kubernetes owns the NLB |
| `resource_gateway_sg.py` | Tagged dedicated RG-only group: no ingress, TCP443 egress to selected IPv4 CIDR |
| Prerequisite Cognito stack | Inbound JWT app/client; may be shared with other labs |

`create_target.py` remains SDK-based because CLI 1.0.3 does not model these
`privateEndpoint` settings. It supports managed VPC, self-managed Lattice,
OpenAPI/API-key, MCP/OAuth, Lambda IAM, `routingDomain` and DYNAMIC listing.
DYNAMIC requires a Gateway without semantic search. No mode falls back to public
egress on incomplete input: select exactly one complete private-mode argument set.

Self-managed mode uses **`--resource-configuration-identifier`**, mapping to
`privateEndpoint.selfManagedLatticeResource.resourceConfigurationIdentifier`.
Pass the Resource Configuration ARN (or `rcfg-*` ID), never a Resource Gateway ARN.
Resource Configurations were SDK-owned in the original sample, not missing CDK outputs.

## Gateway execution IAM

SDK targets cannot implicitly add permissions to a CDK-owned Gateway role.
After creating each provider, generate explicit grants locally and deploy them
**before** creating the target:

```bash
python3 scripts/configure_gateway_permissions.py \
  --credential private-apigw-api-key
agentcore build
agentcore deploy --yes --skip-bootstrap
```

Repeat `--credential` for **all** still-needed providers. This writes the complete
desired grant set, not an accumulating allowlist. Optional Lambda targets use
`--lambda-arn` with exact function ARNs. The CDK app validates the grant account,
region and resource type, rejects wildcards, and uses the provider's exact secret
ARN. Only Gateway workload-identity permissions use the service-required local
identity pattern. No model invocation, configuration-bundle mutation or EC2 ENI
creation grant is added by this wiring. Managed VPC ENIs use the service-linked
role; do not grant generic EC2 create/delete to the Gateway role to work around it.

Deployer permissions are distinct: CloudFormation/CDK asset operations, bounded
`iam:PassRole`, Gateway target/provider management, and the selected networking
services. Provider cleanup also requires read-only `ListGateways`,
`ListGatewayTargets`, `GetGatewayTarget` across the account/region to detect
references; AccessDenied is a failure, not proof of absence. No AdministratorAccess
or blanket runtime policy is prescribed. Review the synthesized template and the
service-linked-role prerequisites before an authorized deployment.

### Historical optional-infrastructure risks

Restoration does not certify every optional stack for production:

- **Retired: `SharedAgentCoreGateway`.** New labs use only the nested CLI Gateway with scoped grants. Removing the duplicate parent definition does not delete or transfer an existing stack. If you deployed it, keep the original checkout for inspection and cleanup; verify its callers before retiring it. Do not point the new lab at that stack.
- `lib/shared/eks-cluster-stack.ts` retains broad LB-controller policies and
  account-root role trust. EKS workloads install packages at pod startup and Helm
  charts are not pinned. Review these separately before the optional EKS run.
- Private-CA/backends retain SSM managed permissions, demo API keys and certificate
  import permission scoped to this account/region. They are isolated lab resources,
  not production auth examples. Public health endpoints are for ALB checks; item
  routes retain their original checks where implemented.
- Historical internal-ALB listeners can add default-open 0.0.0.0/0:443 rules despite
  CIDR-only comments. Internal is not the same as CIDR-only enforcement; review
  those optional listeners before cloud use. This port does not redesign them.
- Retained SGs, EKS manifests, DNS records and CA resources require explicit cleanup.
  The VPC's pre-delete Lambda now **only reports remaining groups** and never
  deletes other stacks' resources. Never use `cdk deploy --all` or `destroy --all`.

## Deployment and recovery safety

Follow [prerequisites](../00-prerequisites/README.md) one selected lab at a time.
Use Bash with `set -euo pipefail`, the correct account/profile/region and unique,
unshared resource names. Stop on the first error. Preserve the existing bootstrap;
use `--skip-bootstrap` where already initialized. The tutorial does not need
account-level Transaction Search setup. Record the current CLI preference with
`agentcore config transactionSearch`, set `agentcore config transactionSearch false`
before deployment, and keep it disabled through cleanup. Restore your prior CLI
preference afterwards. This is a user-wide CLI preference, not an AWS account change.

Use project-specific deployer and execution-role permissions from the
[AWS CLI permissions guidance](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).
Check the complete CDK/CloudFormation role chain; a non-admin caller delegating to
an administrator-level execution role is not least-privilege validation. Bootstrap
and shared-role provisioning are separate platform-owner tasks.

The original tracked configs/targets/state were empty. The missing nested scaffold
was rebuilt from pinned v1 assets; native migration rejected its old CDK pin.
Keep `migratedFrom: "v1"` for legacy naming. **Do not overwrite a deployed v0 copy
with this empty scaffold or erase real state.** Existing deployments need their
own reviewed templates, resource-ID/ownership comparison and rollback plan.

SDK helpers store mode-0600 records under `.vpc-state/`, separate from `.cli/`.
They do not persist credential values. Creation records the caller account through
STS; cleanup/resume verifies the caller still matches that account and the recorded
region before treating NotFound as absence. Legacy records without `accountId`
require explicit reconciliation, not automatic adoption. Protect and back up both
directories; do not run two writers concurrently. No same-name resource is silently
adopted. An EKS-created RG group is additionally bound to the endpoint operation
ID and an exclusively reserved child token. Parent cleanup validates that binding,
exact group ID/VPC/account/region and the remote ownership tag before touching DNS
or the group. Use endpoint cleanup for these children, not the standalone SG CLI.
Interrupted reservations without a recorded EC2 ID remain fail-closed for deliberate
reconciliation; never delete or replace a ledger to bypass a binding mismatch.
A recorded child ID can safely complete a missing parent ID journal only after the
original reservation/generation and actual group identity have been verified.
A failed target, timeout or permission error exits nonzero and retains the IDs.
Provider creation also records the returned secret ARN and the modeled `createdTime`
from a matching Get response. Cleanup requires both, verifies them on every Get,
and journals deletion intent before the name-based API. Accepted/uncertain deletes
are only polled on retry; replacement/missing generation evidence fails closed.
Do not copy current generation fields into an old record to adopt a reused name.
The service has no conditional generation-delete API: use one writer and never
concurrently replace the named provider between its final check and deletion.
Retry a recorded target without changing its configuration:

```bash
python3 scripts/create_target.py --name private-apigw --resume
```

A lost create response reuses the recorded idempotency token. A known target is
polled, not recreated. If an Identity/Lattice/proxy create response was lost,
reconcile the recorded name and pending operation with the selected account before
taking any deletion action; uncertain-create records refuse cleanup rather than
reporting success without an identifier. Never infer ownership merely from a
matching name. Preserve failed records.
Use environment variables `API_KEY_VALUE` / `OAUTH_CLIENT_SECRET` instead of secret
command-line arguments; do not put credentials in endpoint URLs.

## Cleanup

1. Delete only recorded SDK targets with `delete_target.py --name ...`. It waits
   for **confirmed absence**, not a fixed sleep. Keep the audit record.
2. Delete only unshared recorded providers. Cleanup refuses live/pending local
   references and scans every Gateway/target page for remote references.
3. For static-IP and legacy no-SG NLB modes, never omit RG security groups: the
   prerequisite default SG is deliberately empty. Use the owned dedicated group
   (external HTTPS: TCP443 to 0.0.0.0/0 via the existing NAT; internal NLB: TCP443
   to the VPC CIDR). Delete targets first, then the tagged group after ENI release.
   EKS DNS deletion journals its exact change ID and checks alias absence on retry,
   rather than repeatedly deleting a reusable record name.
4. Remove SDK proxies, EKS aliases/rules and self-managed Lattice resources in the
   lab's documented order. Remove RAM sharing before deleting Account B's Resource
   Configuration; wait for AgentCore associations to disappear. Managed Lattice
   resources remain service-owned.
5. Clear generated Gateway grants after target absence, then remove the Gateway:

```bash
python3 scripts/configure_gateway_permissions.py --clear
agentcore remove gateway --name vpc-egress-gateway
agentcore deploy --yes --skip-bootstrap
```

An empty project makes CLI v1 delete its stack. Finally destroy dedicated parent
resources in reverse dependency order. Delete Kubernetes Services and verify NLB
absence **before** deleting their cluster (retained manifests do not do this).
Delete only ledgered retained SGs after ENIs are gone; never delete every SG in a
VPC. Verify logs, secrets/providers, certificate/SSM material, DNS, ENIs, NAT/EIPs
and CA state; preserve shared Cognito/bootstrap. CA deletion may have a recovery
window—report its actual state rather than claiming immediate absence.

Archive a completed ledger only after absence verification if you need to reuse
the names. Config removal and successful delete requests alone are not cleanup
proof. **Cloud deployment, private connectivity, auth negatives and cloud cleanup
were NOT RUN during this migration. No optional mode is claimed cloud-tested.**
