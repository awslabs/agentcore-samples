# infrastructure (0_public)

Prod-only AgentCore Runtime benchmark environment. Derived from the original
`infrastructure/`, with two changes:

- **Prod only.** Every script talks to the public AgentCore endpoints only;
  there is no stage switch.
- **AgentCore Runtime only.** This environment compares AgentCore Runtime's
  own deployment paths and settings against each other, not against any
  other compute option.

The `base-agent` (a minimal FastAPI echo server) is deployed to AgentCore
Runtime two ways, each testable under both managed-compute settings:

- **zip** (direct code) — `deploy-agentcore-zip.sh`. Scales new sessions far
  faster than the container path.
- **container** — `build-and-push.sh` + `deploy-agentcore.sh`. Also testable
  at five image sizes.

## Scripts

| Script | Purpose |
|--------|---------|
| `common.sh` | Shared config + helpers (sourced by the others). |
| `build-and-push.sh` | Build the base-agent image at a given `IMAGE_SIZE` and push to ECR. |
| `deploy-agentcore.sh` | AgentCore Runtime via **container** image (ECR), any `IMAGE_SIZE`. |
| `deploy-agentcore-zip.sh` | AgentCore Runtime via **direct code (zip)**. |
| `agentcore_boto3.py` | Create/update/find-existing-runtime via plain boto3 (this repo's own venv), used instead of the `aws` CLI so a just-released field is usable as soon as it's `pip install`-ed, without waiting for a CLI release. |
| `cleanup.sh` | Deletes everything the deploy scripts can create (runtimes, ECR repo/images, S3 code bucket, IAM roles). Dry run by default; `--yes` to actually delete. See the top-level README's Cost section. |

## Platform version: V1 (Original Runtime) vs V2 (New Runtime)

Both deploy scripts accept `AGENTCORE_PLATFORM_VERSION` (`V1` or `V2`),
which sets the API's own `platformVersion` field on create/update — the name
now matches the field it sets. (`AGENTCORE_MANAGED_COMPUTE_VERSION` still
works as a deprecated alias, with a warning, for one release.) When set, it
does two things:

1. The create/update call goes through `agentcore_boto3.py` (this repo's
   venv), so it works as soon as the installed boto3 supports the field,
   regardless of the `aws` CLI's own (separately bundled, possibly older)
   botocore.
2. `_V1` or `_V2` is appended to the runtime's default name automatically, so
   running the same deploy once per version creates two distinctly named
   runtimes ready to compare, without hand-editing `AGENT_RUNTIME_NAME`.

```bash
AGENTCORE_PLATFORM_VERSION=V1 ./deploy-agentcore-zip.sh
AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore-zip.sh
# -> ac_zip_x_lambda_bench_agent_V1 and ..._V2, both live at once
```

## Container image sizes

`build-and-push.sh` and `deploy-agentcore.sh` both take `IMAGE_SIZE`:
`200mb` (default, today's image unchanged), `500mb`, `750mb`, `1gb`, or `2gb`.
Padding is added on top of the base image via a Docker build-arg
(`base-agent/Dockerfile`'s `PAD_MB`), using `/dev/urandom` so it survives
ECR's layer compression instead of vanishing.

```bash
IMAGE_SIZE=750mb ./build-and-push.sh
IMAGE_SIZE=750mb AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore.sh
# -> ac_ctn_x_lambda_agent_750mb_V2
```

`200mb` is a label, not a literal measurement. The unpadded image (plain
`python:3.13-slim`, `fastapi`/`uvicorn`/`pydantic` only, `pip` removed after
install) measured **~227 MB** locally, down from ~318 MB on the original
`uv`/bookworm-slim base. Getting to a literal 200 MB would need an
Alpine/musl base, which risks missing prebuilt arm64 wheels for
`pydantic-core`; not done without checking first. The other four pad amounts
are computed against that ~227 MB floor. Local size measurements were
inconsistent across tools during this check (`docker images`, `docker
history`, and `du` inside the container each gave a different number for the
same image) — before relying on the size ladder, check what ECR actually
reports after a real push:

```bash
aws ecr describe-images --repository-name "${ECR_REPOSITORY}" \
  --query 'imageDetails[].[imageTags[0],imageSizeInBytes]' --output table
```

## Usage

```bash
cd infrastructure

# --- zip, both versions ---
AGENTCORE_PLATFORM_VERSION=V1 ./deploy-agentcore-zip.sh
AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore-zip.sh

# --- container, one size, both versions ---
IMAGE_SIZE=500mb ./build-and-push.sh
IMAGE_SIZE=500mb AGENTCORE_PLATFORM_VERSION=V1 ./deploy-agentcore.sh
IMAGE_SIZE=500mb AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore.sh
```

Each deploy prints the runtime ARN. Force a genuinely new runtime instead of
updating an existing one with the same name via `AGENTCORE_NEW_DEPLOY=1`
(appends a timestamp suffix, since names must be unique per account/region).

## Notes

- IAM roles are created and managed by the deploy scripts with least-privilege
  policies. Re-running a deploy refreshes the role's trust and inline
  permissions policies.
- `agentcore_boto3.py` only targets the public prod control-plane endpoint
  (`bedrock-agentcore-control.<region>.amazonaws.com`); it has no stage
  switch.
