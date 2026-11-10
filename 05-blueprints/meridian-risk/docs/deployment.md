# Deployment

## Prerequisites

Terraform ≥ 1.5, Docker (running), Python 3.13, Node 18+, and AWS credentials
for an account with Amazon Bedrock model access in a Registry-preview Region
(`us-east-1`, `us-west-2`, `ap-northeast-1`, `ap-southeast-2`, `eu-west-1`).

A recent `boto3` is required — the preview APIs are unknown to versions before
1.42. `scripts/bootstrap.sh` handles this.

**AgentCore Registry preview entitlement.** Registry is per-account enrolled,
even for `AdministratorAccess` principals. Before `terraform apply`, confirm the
account is entitled:

```bash
aws bedrock-agentcore-control list-registries --region us-east-1
```

If this errors with `AccessDeniedException`, request preview access from AWS
before deploying — the apply will otherwise fail on `CreateRegistry` after
provisioning ~50 other resources. Gateway, Memory, and Policy do not have this
gate.

## Deploy

```bash
# 1. Credentials — gitignored
cat > .env <<'ENV'
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
ENV

# 2. Local environment
./scripts/bootstrap.sh

# 3. Deployment config. Real tfvars files are gitignored.
cp infra/terraform.tfvars.example infra/terraform.tfvars
#    Set console_user_email to your own address.

# 4. Deploy — builds and pushes the ARM64 agent image, then seeds the Registry
set -a && source .env && set +a && unset AWS_PROFILE
terraform -chdir=infra init
terraform -chdir=infra apply

# 5. Open the hosted console and sign in
terraform -chdir=infra output -raw console_url
terraform -chdir=infra output -raw console_username
terraform -chdir=infra output -raw console_password
```

**`console_user_email` has no default.** With no value, the stack deploys
successfully but creates no Cognito user, and `console_username` reports
`no user created` — there is no way to sign in. This is the one setting a new
deployment must supply.

The password is generated unless you set `console_user_password`; either way,
read it with `terraform -chdir=infra output -raw console_password`. The account is
created with a permanent password, so the first sign-in is not a reset
challenge.

## Run the console locally against the deployed backend

```bash
python3 scripts/write_env.py       # writes .env.deploy + frontend/.env.local
AUTH_DISABLED=1 ./scripts/dev.sh   # http://localhost:5173, auth bypassed
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `inference_route` | `gateway` | `gateway` routes model calls through the Gateway's `/inference` target; `direct` calls Bedrock. The agent code is identical either way. |
| `gateway_model_id` | `bedrock-mantle/deepseek.v3.1` | Model id as the inference connector advertises it. List options with `GET {gateway}/inference/v1/models`. DeepSeek by default — see the model-access note below. |
| `model_id` | a Bedrock inference profile | Used only by the `direct` route. The two routes need different identifiers for the same intent. |
| `policy_engine_mode` | `ENFORCE` | `ENFORCE` denies policy violations; `LOG_ONLY` evaluates and logs them. Switch to `LOG_ONLY` before adding or widening a policy — `ENFORCE` is default-deny. |
| `registry_auto_approval` | `false` | Left off so the demo can walk `DRAFT → PENDING_APPROVAL → APPROVED`. |
| `enable_registry_oauth_demo` | `false` | Adds the optional OAuth flows — a JWT-authorized twin runtime + gateway + registry, all gated. See [OAuth flows (optional)](#oauth-flows-optional). |

## OAuth flows (optional)

The base stack authorizes the Gateway with AWS IAM (SigV4). An optional, gated
slice adds OAuth-authenticated variants — the "discover, govern, and reuse over
OAuth" story from AWS's Agent Registry Show & Tell. It reuses the same KYC tools
and agent and stays off unless you set `enable_registry_oauth_demo`:

```bash
terraform -chdir=infra apply -var enable_registry_oauth_demo=true
# or: TF_VAR_enable_registry_oauth_demo=true ./scripts/deploy.sh
```

Three flows, each with a runnable, self-verifying script (`200` with a valid
token, `401`/`403` without):

| Flow | Trace it in |
|---|---|
| **Auto-populate (URL sync)** — the Registry calls an OAuth-protected MCP gateway (client-credentials) and auto-discovers its tools into a record, no hand-authored list. | `scripts/seed_sync_record.py`, `infra/registry_oauth_sync.tf` |
| **Discover → invoke** — a consumer finds an agent, mints an M2M OAuth token via AgentCore Identity, and calls it with a bearer token (no SigV4 on the agent call). | `scripts/discover_and_invoke_via_oauth.py`, `infra/registry_oauth_demo.tf` |
| **OAuth discovery** — a JWT/OAuth-authorized Registry searched with a bearer token instead of IAM. | `scripts/discover_via_oauth_registry.py`, `infra/registry_oauth_discovery.tf` |

The credential provider references a customer-owned Secrets Manager secret
(`scripts/manage_oauth_provider.py`). The grant is machine-to-machine
(client-credentials) — service identity, not an end-user identity — and needs the
same Registry preview entitlement as the base stack. Preview-specific behaviors
(the `GetResourceOauth2Token` IAM chain, raw-bearer runtime invocation, the
JWT-registry search endpoint) are in [preview-api-notes.md](preview-api-notes.md).

## Teardown

```bash
terraform -chdir=infra destroy
```

Registry records are purged first, since a registry cannot be deleted while it
still holds records.

## Troubleshooting

### `AccessDeniedException: not authorized to perform: bedrock-agentcore:CreateRegistry`

AgentCore Registry is a preview service with per-account enrollment. Even a
principal with `AdministratorAccess` gets denied on unentitled accounts. The
apply will typically fail at the very end, after ~50 other resources have been
provisioned. Fix: request Registry preview access from AWS, then re-apply.
Nothing in code will change this — it is granted at the AWS service level.

Confirm entitlement with:

```bash
aws bedrock-agentcore-control list-registries --region us-east-1
```

If it returns JSON, you are entitled. If it errors with `AccessDeniedException`,
you are not.

### `Provider produced inconsistent result after apply` on `aws_bedrockagentcore_gateway_target.kyc_tools`

Full message includes
`.metadata_configuration[0].allowed_request_headers: was null, but now [...]`.
This is a provider bug: when a policy engine is attached to the gateway, the
service auto-populates a service-reserved `X-Amzn-*` request header on the
target, but the provider also forbids setting `X-Amzn-*` headers from config
— so no static value matches. `lifecycle.ignore_changes` prevents the resulting
replace-churn on subsequent plans, but does not silence the initial create's
apply-time check. The target IS successfully created despite the error. Fix:
re-run `terraform apply` once — the second pass finds the target already in
state (with the service-injected header) and the plan is clean. `deploy.sh`
automates this: it untaints every gateway target and retries the apply (up to six
times, pausing between so IAM/service races settle) so a fresh-account deploy
converges without manual steps.

### `403 "not available for this account"` on a model call

Assessments fail after the specialists start, with:

```
Error code: 403 - anthropic.claude-sonnet-5 is not available for this account.
```

The infrastructure is fine — the gateway, tools, and policy all work; only the
model call is blocked. The `bedrock-mantle` connector invokes Anthropic models
on-demand, and the newer Claude models (sonnet-5, opus-*, haiku-4.5) are
**per-account entitlements** granted through AWS Sales. There is no console
toggle or IAM change that clears this — the Anthropic First-Time-Use form
explicitly does not apply to the `bedrock-mantle` endpoint. To confirm which
models an account can reach through the connector:

```bash
awscurl --service bedrock-agentcore --region us-east-1 \
  "https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/inference/v1/models"
```

Every model there is *advertised*; invoke one to see whether the account is
entitled. DeepSeek and Mistral are openly available, which is why
`gateway_model_id` defaults to DeepSeek. For Claude on an unentitled account,
set `inference_route = "direct"` — that path uses a standard Bedrock inference
profile (`model_id`), which most accounts can invoke, at the cost of bypassing
the gateway for model calls.

One IAM prerequisite is easy to miss and looks identical: the gateway role
needs `aws-marketplace:Subscribe` (the third statement of
`AmazonBedrockMantleInferenceAccess`) so Bedrock can auto-subscribe a
third-party model on its *first* invocation. Without it, even an entitled model
fails its first call. This repo's `infra/gateway.tf` includes it.

### "Unexpected Identity Change" on apply

```
Error: Unexpected Identity Change
  with aws_bedrockagentcore_policy_engine.kyc
During the read operation, the Terraform Provider unexpectedly
returned a different identity than the previously stored one.
This is always a problem with the provider and should be reported
to the provider developer.

Current Identity: ... "account_id":tftypes.String<"111122223333"> ...
New Identity:     ... "account_id":tftypes.String<"444455556666"> ...
```

Despite the message, this is **not** a provider bug. It means the local
`infra/terraform.tfstate` was written against a different AWS account than your
current credentials. The provider derives each resource's identity from the
*current* caller's account id plus the stored resource id, so the check fails
before any API call is made — which is why the resource ids in both identities
are identical while only the account differs.

Terraform state is not portable between accounts. **`deploy.sh` handles this
automatically**: when the state's account differs from your current credentials,
it archives the old state to `infra/.state-archive/<old-account-id>-<timestamp>/`
and deploys fresh. Running `terraform` by hand, do the same manually — set the old
state aside rather than deleting it, since it is the only record of the resources
still running in the old account and you need it to tear them down:

```bash
mkdir -p "infra/.state-archive/<old-account-id>"
mv infra/terraform.tfstate* "infra/.state-archive/<old-account-id>/"
```

Also clear the generated files that cache the old stack's identifiers, or the
local console will keep talking to it:

```bash
rm -f .env.deploy infra-outputs.json frontend/public/config.json frontend/.env.local
```

Then `terraform -chdir=infra plan` should report creates only. To destroy the
old stack later, restore its state file, point your credentials at that account,
and run `destroy`.

`infra/.state-archive/` is gitignored, since state files contain account
identifiers and resource detail.

## Notes from deploying the hosted console

- **API Gateway cannot front this API.** Its integration timeout is a hard 30s and
  assessments run 25–60s. The API is a Lambda Function URL instead.
- **Lambda response streaming is Node.js-only natively.** Python needs the Lambda
  Web Adapter (`AWS_LWA_INVOKE_MODE=response_stream`), which is why the API ships
  as a container image rather than a zip.
- **Lambda rejects buildx's default manifest.** `docker build` emits an OCI image
  index with an attestation manifest, and `CreateFunction` fails with *"image
  manifest ... media type ... is not supported"*. Build with `--provenance=false
  --sbom=false`.
- **Some accounts block Function URLs with `authorization_type = NONE`** — unsigned
  requests get 403 before reaching the function, even with a wide-open resource
  policy. So the URL uses `AWS_IAM` and the browser SigV4-signs, using credentials
  from a Cognito identity pool.
- **Function URL invocation needs BOTH `lambda:InvokeFunctionUrl` and
  `lambda:InvokeFunction`.** Granting only the former returns a bare 403 while
  `aws iam simulate-principal-policy --action-names lambda:InvokeFunctionUrl`
  reports `allowed` — which reads exactly like a signing bug and cost the most
  time of anything here. Diffing against a temporary `AdministratorAccess`
  attachment is what isolated it.
- **Don't sign `x-amz-content-sha256` in the browser.** Every signed header must be
  reproduced byte-for-byte on the wire; the browser normalizes or drops that one,
  invalidating the signature. Sign only `host`, `x-amz-date`, and
  `x-amz-security-token`, which is what botocore does.
- **The ID token cannot ride in `Authorization`** once SigV4 owns that header, so it
  is sent as `X-Id-Token`.
- **Don't let both the Function URL and the app emit CORS headers.** Two
  `Access-Control-Allow-Origin` values makes browsers reject the response
  outright; the app skips its CORS middleware when running in Lambda.
- **The Function URL's `allow_headers` must list every header the browser sends**,
  including the `x-amz-*` signature headers. Otherwise the preflight returns 200
  with no CORS headers at all.
- **Emit an SSE event before calling the Runtime.** `invoke_agent_runtime` does not
  return until the agent's first byte, so without a prior yield the client sees
  nothing for tens of seconds.
- **Images are content-addressed in ECR**, so a code change updates each function
  in place rather than replacing it — which would regenerate the Function URL
  hostname and the Runtime ARN that other resources reference.

Constraints met against the AgentCore APIs themselves are in
[preview-api-notes.md](preview-api-notes.md).
