# Deploy a framework example on AgentCore Runtime

Use **CLI 1.0.3** to deploy **one** of four examples: [Google ADK search](adk/), [OpenAI Agents handoffs](openai-agents/), [Strands streaming with OpenAI](strands-agents/), or [Pydantic AI with Bedrock](pydanticai-agents/). The deployment machine never needs a model API key. For local-only development, start with the example's README.

## Before you start

Use Bash, Python 3.13, uv, AWS CLI v2, Node.js and npm. Select a dedicated sandbox with an approved short-lived profile, an already bootstrapped CDK environment and a Region supporting Runtime CodeZip and Identity. AWS, model and hosted search usage can incur charges.

- **Bedrock-only path:** Pydantic AI uses the runtime execution role, not a provider key. Select a supported `BEDROCK_MODEL_ID` and scope model invocation to that model or inference profile and its required destination models. Do not create or grant access to an Identity provider for this variant.
- **External-model paths only — outbound provider:** ask its owner for the name of an AgentCore Identity API-key provider for Gemini or OpenAI. If you need a new one, have the owner create it with a unique name; stop on collision. No separately created Secrets Manager secret is required. These projects do **not** declare, update or delete providers. Do not run `agentcore add credentials` here.
- **Execution role:** use an approved role in that account that trusts `bedrock-agentcore.amazonaws.com` for the intended account/runtime. Scope access to the Runtime logs, its CodeZip asset (including S3-mediated decrypt if needed), and, **only for external models**, Identity retrieval for this workload, vault, provider and backing secret. See [Runtime permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) and [Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html). No role needs provider management; only the Pydantic AI variant needs Bedrock inference. The deployer separately needs permission to pass the role. The project imports it without modifying it.

## Build a fresh project

Start **in the example directory**, in a shell without a Gemini/OpenAI key. Set `AWS_PROFILE`, `AWS_REGION`, `ROLE_ARN` and either `PROVIDER_NAME` (the **name**, never the key) or `BEDROCK_MODEL_ID` for Pydantic AI. Use a fresh directory and project name for every new deployment; keep the resulting directory for cleanup.

```bash
set -euo pipefail
: "${AWS_PROFILE:?}" "${AWS_REGION:?}" "${ROLE_ARN:?}"
sample=$PWD
case "${sample##*/}" in
  adk) runtime=adk; provider_var=GEMINI_PROVIDER_NAME; files=(adk_agent_google_search.py provider_credentials.py pyproject.toml) ;;
  openai-agents) runtime=handoff; provider_var=OPENAI_PROVIDER_NAME; files=(openai_agents_handoff_example.py runtime_app.py pyproject.toml) ;;
  strands-agents) runtime=stream; provider_var=OPENAI_PROVIDER_NAME; files=(strands_openai_identity.py pyproject.toml) ;;
  pydanticai-agents) runtime=pydantic; provider_var=BEDROCK_MODEL_ID; files=(pydantic_bedrock_claude.py pyproject.toml) ;;
  *) echo 'Start in one of the four listed sample directories.' >&2; exit 1 ;;
esac
account=$(aws sts get-caller-identity --query Account --output text)
[[ "$ROLE_ARN" == arn:*:iam::"$account":role/* ]]
agentcore() { npx --yes --package=@aws/agentcore@1.0.3 -- agentcore "$@"; }
agentcore --version  # expect 1.0.3
project=Example$(python3 -c 'import secrets; print(secrets.token_hex(6))')
cd "$(mktemp -d)"
agentcore create --name "$project" --template empty --skip-install --skip-git
cd "$project"; pwd
if [[ $runtime == pydantic ]]; then value=${BEDROCK_MODEL_ID:?}; else value=${PROVIDER_NAME:?}; fi
config=$(python3 -c 'import json,sys; print(json.dumps({sys.argv[1]:sys.argv[2]}))' "$provider_var" "$value")
agentcore add runtime --name "$runtime" --language python --role-arn "$ROLE_ARN" --environment-variables "$config"
for file in "${files[@]}"; do cp "$sample/$file" "app/$runtime/"; done
python3 - <<'PY'
import json
from pathlib import Path
p = Path('agentcore/agentcore.json')
spec = json.loads(p.read_text())
assert not spec['credentials'] and len(spec['runtimes']) == 1
spec['runtimes'][0]['instrumentation'] = {'enableOtel': False}
p.write_text(json.dumps(spec, indent=2) + '\n')
PY
npm pkg set --prefix agentcore/cdk 'devDependencies.aws-cdk=2.1149.0'
npm install --prefix agentcore/cdk --ignore-scripts --save-exact aws-cdk-lib@2.278.0
agentcore build --json
```

Review `agentcore/cdk/cdk.out/`: expect one Runtime, its imported role ARN, the project-specific ZIP, and no provider, secret or role resources. Before first deploy, reject a saved target or a stack name already in use. Use a dedicated project with no concurrent deployer; this check isn't a distributed lock.

```bash
[[ ! -e agentcore/.cli/deployed-state.json ]]
python3 -c "import json; assert json.load(open('agentcore/aws-targets.json')) == []"
stack="AgentCore-${project}-default"
existing=$(aws cloudformation list-stacks --region "$AWS_REGION" --output json)
python3 -c 'import json,sys; assert not any(s["StackName"] == sys.argv[1] and s["StackStatus"] != "DELETE_COMPLETE" for s in json.load(sys.stdin)["StackSummaries"]), "stack already exists"' "$stack" <<< "$existing"
agentcore config transactionSearch  # note your current user-wide preference
agentcore config transactionSearch false
agentcore deploy --region "$AWS_REGION" --skip-bootstrap
agentcore status --json
```

## Invoke and clean up

Use the command for **the example you deployed**:

```bash
# Pydantic AI: use Bedrock and return a JSON string; no Identity credential needed
agentcore invoke --runtime pydantic --payload '{"prompt":"What is the current UTC date and the simulated forecast?"}'

# ADK: ask for the answer and provider-supplied grounding metadata
agentcore invoke --runtime adk --user-id demo-user --payload '{"prompt":"What was announced at Google I/O?","include_grounding":true}'

# Strands: stream the answer text (the weather tool is a fixture)
agentcore invoke --runtime stream --user-id demo-user --accept text/event-stream --payload '{"prompt":"What is the demo forecast for Seattle?"}'

# OpenAI Agents: route a dining question to the food specialist
agentcore invoke --runtime handoff --user-id demo-user --payload '{"prompt":"Where should I eat in Tokyo?"}'
```

The caller must be authorized for IAM-authenticated invocation on behalf of the user. `--user-id` is not proof of identity. For the three external-model examples, Runtime supplies the workload token; if it is absent the agent fails closed. Use fictional data: prompts can go to third parties.

From **only the generated project**, verify the original AWS account, Region, saved target and stack with `agentcore status --json`. Run `agentcore remove all --yes`, then `agentcore deploy --yes --skip-bootstrap`, and confirm stack deletion. Restore your previous CLI Transaction Search preference. The imported role and provider remain with their owners. Check retention policy for service logs and individual CDK assets; never empty a shared bucket. If you created a dedicated provider, retire it separately after verifying its identity and consumers, and revoke the model key if no longer needed.
