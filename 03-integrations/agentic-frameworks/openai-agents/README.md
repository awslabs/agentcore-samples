# OpenAI Agents SDK handoffs on AgentCore Runtime

A travel assistant routes trip planning to a Travel Expert and dining questions to a Food Expert. Each specialist has OpenAI's hosted web search tool. The triage decision and search results are model-dependent. Both this example and the simpler `openai_agents_hello_world.py` accept `{"prompt": "..."}` and return `{"result": "..."}`. This is the OpenAI **Agents SDK**, not the Assistants API or a Strands model adapter.

## Try the handoff without credentials

From this sample directory, with uv and Python 3.13:

```bash
uv sync --frozen
uv run --frozen pytest -q test_handoff.py
```

The tests drive both handoffs through the real SDK with controlled HTTP responses, without calling OpenAI or AWS. To try a paid local invocation, start a **private terminal on a trusted machine**, read your own OpenAI key without echoing it, and run:

```bash
(read -rsp 'OpenAI key: ' OPENAI_API_KEY && echo && export OPENAI_API_KEY LOCAL_DEV=1 &&
 uv run --frozen uvicorn openai_agents_handoff_example:app --host 127.0.0.1 --port 8080)
```

From a second terminal, POST `{"prompt":"Find a vegetarian restaurant in Tokyo"}` to `http://127.0.0.1:8080/invocations` as JSON. Stop the server with Ctrl-C. Local HTTP is unauthenticated: do not expose the port, use a shared host, or browse untrusted sites while serving. Never set `LOCAL_DEV` in the deployed Runtime. Do not enable debug, request-body logging or tracing of customer data.

## Deploy with AgentCore CLI 1.0.3

Use Bash, Node.js and a dedicated sandbox with an approved AWS profile, a bootstrapped CDK environment, and a Region supporting Runtime CodeZip and Identity. AWS and OpenAI charges apply. Use CLI **1.0.3** for these commands. No model key is needed on the deployment machine.

**Choose an outbound Identity API-key provider:** ask its owner for the **name** of an existing provider authorized for this agent, or have the owner create a **new** API-key credential provider in AgentCore Identity using the OpenAI key. For a new provider, use a unique name and stop on collision; do not update a same-name provider. No separate customer-created Secrets Manager secret is required. The provider is managed outside this project: **do not run `agentcore add credentials`**. Runtime code retrieves the key using `@requires_api_key` and the invocation's workload access token; it does not set a shared environment variable.

Use an owner-approved **Runtime execution role** in the target account. It must trust `bedrock-agentcore.amazonaws.com` for the intended account/runtime and permit CodeZip startup (read its CDK asset, S3-mediated decrypt if needed), Runtime logging and Identity API-key retrieval for this workload, its token vault/provider and backing secret only. Review the [Runtime permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) and [Identity documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html) with the role owner. The role does not need to create, update or delete providers or invoke Bedrock models. The deployer separately needs permission to pass that role. Do not use administrator access. The CLI imports the role; this project neither grants permissions to it nor deletes it.

From the sample directory, in a **fresh shell with no OpenAI key**, set `AWS_PROFILE`, `AWS_REGION`, `ROLE_ARN` and `PROVIDER_NAME`. Only the provider **name**, not a key, belongs in `PROVIDER_NAME`.

```bash
set -euo pipefail
: "${AWS_PROFILE:?}" "${AWS_REGION:?}" "${ROLE_ARN:?}" "${PROVIDER_NAME:?}"
account=$(aws sts get-caller-identity --query Account --output text)
[[ "$ROLE_ARN" == arn:*:iam::"$account":role/* ]]
agentcore() { npx --yes --package=@aws/agentcore@1.0.3 -- agentcore "$@"; }
agentcore --version
sample=$PWD
project=Handoff$(python3 -c 'import secrets; print(secrets.token_hex(6))')
cd "$(mktemp -d)"
agentcore create --name "$project" --template empty --skip-install --skip-git
cd "$project"; pwd  # keep this directory and its target state for cleanup
config=$(python3 -c 'import json,sys; print(json.dumps({"OPENAI_PROVIDER_NAME":sys.argv[1], "OPENAI_AGENTS_DISABLE_TRACING":"1"}))' "$PROVIDER_NAME")
agentcore add runtime --name handoff --language python --role-arn "$ROLE_ARN" --environment-variables "$config"
cp "$sample"/{runtime_app.py,openai_agents_handoff_example.py,pyproject.toml} app/handoff/
python3 - <<'PY'
import json
from pathlib import Path
p = Path('agentcore/agentcore.json')
spec = json.loads(p.read_text())
assert not spec['credentials'] and len(spec['runtimes']) == 1
spec['runtimes'][0]['instrumentation'] = {'enableOtel': False}
p.write_text(json.dumps(spec, indent=2) + '\n')
PY
# Pair the patched CDK library with a CDK CLI that reads its cloud assembly.
# These are project-local build tools; AgentCore CLI and @aws/agentcore-cdk stay at 1.0.3.
npm pkg set --prefix agentcore/cdk 'devDependencies.aws-cdk=2.1149.0'
npm install --prefix agentcore/cdk --ignore-scripts --save-exact aws-cdk-lib@2.278.0
agentcore build --json
```

Review the synthesized Runtime's code, entrypoint, imported role, and environment in `agentcore/cdk/cdk.out/`. Expect no credential provider, secret or IAM role resource. Before **first deploy**, reject saved deployment state or a same-name stack. Use a dedicated sandbox with no other deployer operating on this project; the name check is not a distributed lock.

```bash
[[ ! -e agentcore/.cli/deployed-state.json ]]
python3 -c "import json; assert json.load(open('agentcore/aws-targets.json')) == []"
stack="AgentCore-${project}-default"
existing=$(aws cloudformation list-stacks --region "$AWS_REGION" --output json)
python3 -c 'import json,sys; assert not any(s["StackName"] == sys.argv[1] and s["StackStatus"] != "DELETE_COMPLETE" for s in json.load(sys.stdin)["StackSummaries"]), "stack already exists"' "$stack" <<< "$existing"
agentcore config transactionSearch false  # user-wide CLI preference; note previous value first
agentcore deploy --region "$AWS_REGION" --skip-bootstrap
agentcore status --json
agentcore invoke --runtime handoff --user-id demo-traveler --payload '{"prompt":"Where should I eat in Tokyo?"}'
```

Use IAM-authenticated invocation. The caller must also be authorized to invoke on behalf of this user; `--user-id` is not proof of a customer's identity. Runtime supplies the workload token for outbound Identity access. If it is absent, the app fails closed rather than using a local key. Requests and tool inputs may go to OpenAI; use fictional data.

## Cleanup

From **only the fresh project created above**, confirm the same AWS account, Region, saved target and stack (`agentcore status --json`). Run `agentcore remove all --yes`, then `agentcore deploy --yes --skip-bootstrap` and confirm stack deletion. Restore your prior CLI Transaction Search preference. Shared CDK assets and service logs can remain; follow your retention policy, never empty a shared bucket. The imported execution role and API-key provider remain with their owners. If you created a dedicated provider, retire it separately after verifying its identity and consumers, then revoke the OpenAI key if no longer needed.
