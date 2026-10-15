<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Setup / teardown helper scripts

Self-documenting helpers that stand the app up and tear it back down. Each **layer**
is ONE script that takes `up` or `down`, so create and destroy of a resource live
in the same file and cannot drift. Every script prints **what it is about to do**
and the **exact command** it runs against your AWS account.

```
_lib.sh              shared logging + config (sourced by all scripts)
00-preflight.sh      read-only: verify tools + AWS identity + current state
01-foundation.sh     up|down   CloudFormation: Cognito pools + Identity Pool + 3 IAM roles + groups
02-config.sh         up|down   local .env + state files (from stack outputs / reset)
03-registry.sh       up|down   registry (CUSTOM_JWT) + 9 tagged records + 3 persona users
04-run-local.sh                build + preview the SPA on http://127.0.0.1:4173/
05-site.sh           up|down   public site: S3 + CloudFront + OAC (+ bucket policy)
06-sample-mcp-gateway.sh up|down  sample MCP server: Lambda + AgentCore Gateway (MCP,
                               CUSTOM_JWT) + AgentCore Identity OAuth2 credential
                               provider on the same Cognito pool + a registry record
                               synchronized from the gateway's MCP endpoint
99-all.sh            up|down   every layer in order (up) / reverse (down)
```

## Bring it up

```bash
cd deploy/setup
./00-preflight.sh          # optional sanity check
./01-foundation.sh up      # (you run this - see safety note)
./02-config.sh up
./03-registry.sh up        # prints the generated persona password once
```

Layer 03 is the **administrator step the UI deliberately does not perform**: it creates
the registry, choosing its authorization model. By default that is `CUSTOM_JWT` with the
discovery URL pointed at the user pool from layer 01, so consumers (and any external MCP
client) authorize with a Cognito bearer token. Use `REGISTRY_AUTH_MODE=AWS_IAM` for the
SigV4 variant. **A registry's authorizer type and JWT discovery URL are immutable** —
changing your mind means tearing layer 03 down and back up, which creates a new registry.

Layer 03 also writes `VITE_REGISTRY_ID` and `VITE_REGISTRY_AUTH_MODE` into
`frontend/.env`; the SPA must agree with the registry, since the two modes use different
credentials for discovery.

```bash
./04-run-local.sh          # local validation (optional)
./05-site.sh up            # public CloudFront URL
# or all at once (after 01, which you run):
./99-all.sh up
```

## Tear it down

```bash
cd deploy/setup
./99-all.sh down           # 05 -> 06 -> 03 -> 01 -> 02, one confirmation (FORCE=1 skips)
# or a single layer:
./05-site.sh down          # CloudFront (disable->wait->delete) + OAC + S3 bucket
./06-sample-mcp-gateway.sh down  # sample record, gateway, credential provider, Lambda + log group, roles
./03-registry.sh down      # delete records, registry, AND the seeded persona users
./01-foundation.sh down    # delete the CloudFormation stack (Cognito + IAM)
./02-config.sh down        # reset local .env + state
```

`01-foundation.sh down` deletes the whole CFN stack by default (clean slate, also
removes the stack record). Pass `DIRECT=1` (on the layer script or `99-all.sh`) to
delete the Cognito/IAM resources directly instead — only for a no-stack / out-of-band
cleanup; that leaves the stack record orphaned. Every `down` reads ids only from
`deploy/state/outputs.env` (same-named variables in your shell are ignored) and skips
anything already gone.

## Config (override via environment)

| Var | Default | Meaning |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Region for every call |
| `APP_NAME` | `agentregistry-ui` | Resource-name prefix (CFN param). Lowercase letters, digits and single hyphens, because it also names the S3 bucket, the Cognito domain and the gateway |
| `STACK_NAME` | `$APP_NAME` (`agentregistry-ui`) | CloudFormation stack name |
| `REGISTRY_NAME` | `AgentRegistryDemo` | Name of the registry `03-registry.sh up` creates |
| `PERSONA_PASSWORD` | (generated) | Password for the 3 seeded users (`03-registry.sh up`) |
| `REGISTRY_AUTH_MODE` | `CUSTOM_JWT` | Registry inbound auth for consumers: `CUSTOM_JWT` (Cognito bearer token) or `AWS_IAM` (SigV4). Immutable once the registry exists (`03-registry.sh up`) |
| `SITE_BUCKET` | `<APP_NAME>-site-<account>` | S3 bucket for the site (`05-site.sh`) |
| `FORCE` | `0` | `1` skips the `99-all.sh down` confirmation |
| `DIRECT` | `0` | `1` = `01-foundation.sh down` deletes Cognito/IAM without the stack |
| `SKIP_SAMPLE` | `0` | `1` = `99-all.sh` omits layer 06 (the sample MCP server + gateway) in both directions |

## Required permissions

| Layer | Needs |
|---|---|
| `01-foundation.sh` | `cloudformation:*` on the stack, plus `iam:CreateRole` / `PutRolePolicy` / `DeleteRole` / `DeleteRolePolicy` (the stack creates **named** IAM roles, hence `CAPABILITY_NAMED_IAM`), and the Cognito user/identity-pool actions |
| `03-registry.sh` | `agent-registry:*` on the registry and its records (incl. `TagResource` for tag-on-create) **plus** the AgentCore workload-identity permissions `CreateRegistry` needs — see the note below — plus `cognito-idp:AdminCreateUser` / `AdminSetUserPassword` / `AdminAddUserToGroup` / `AdminDeleteUser` |
| `05-site.sh` | `s3:CreateBucket` / `PutBucketPolicy` / `PutPublicAccessBlock` / `PutObject` / `DeleteObject` / `DeleteBucket`, and `cloudfront:CreateOriginAccessControl` / `CreateDistribution` / `UpdateDistribution` / `DeleteDistribution` / `CreateInvalidation` |
| `06-sample-mcp-gateway.sh` | `lambda:CreateFunction` / `UpdateFunctionCode` / `DeleteFunction`, `iam:CreateRole` / `PutRolePolicy` / `AttachRolePolicy` / `DeleteRole` (two service roles), `bedrock-agentcore:CreateGateway` / `CreateGatewayTarget` / `Get*` / `List*` / `Delete*`, `bedrock-agentcore:CreateOauth2CredentialProvider` / `Get` / `Delete` (the client secret is stored in Secrets Manager on your behalf), `cognito-idp:CreateResourceServer` / `CreateUserPoolClient` / `CreateUserPoolDomain` / `DescribeUserPoolClient` (+ the matching deletes), and `agent-registry:CreateRegistryRecord` / `TagResource` |

`AgentRegistryFullAccess` plus administrator-level CloudFormation/IAM/S3/CloudFront
access covers all of it. `AgentRegistryFullAccess` matters specifically for
`03-registry.sh`: `CreateRegistry` asynchronously provisions an AgentCore workload
identity, so a principal holding only `agent-registry:*` leaves the registry in
`CREATE_FAILED` with *"Unable to create workload identity because access was denied."*
If a restricted shell refuses one call, the script says so and
keeps going where it safely can — `05-site.sh up`, for example, prints the exact
`put-bucket-policy` command to run separately rather than failing the whole deploy.
