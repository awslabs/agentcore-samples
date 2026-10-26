# 02-strands-agent — Strands agent on AgentCore Runtime

Same shape as the upstream `bedrock-samples / managed-knowledge-bases / 03-use-case-example` notebook, but the agent runs on **AgentCore Runtime** instead of locally. Uses the `agentcore` CLI from `bedrock-agentcore-starter-toolkit`.

## What's here

```
02-strands-agent/
├── README.md
├── fmkb_gateway_strands.py     # the agent — BedrockAgentCoreApp + @app.entrypoint
├── requirements.txt            # in-folder copy required by `agentcore configure`
└── iam/
    ├── runtime-trust-policy.json
    └── runtime-execution-policy.json    # uses bedrock-agentcore:InvokeGateway
```

## Prereqs

1. You've run `01-raw-mcp/setup_gateway.py` and `source ../.env.fmkb-gateway` — that gives you `GATEWAY_URL`, `GATEWAY_ID`, `REGION`, `ACCOUNT_ID`.
2. `pip install bedrock-agentcore-starter-toolkit` for the `agentcore` CLI.

## Provision the runtime execution role

```bash
# from 02-strands-agent/
export AGENT_NAME=fmkb_gateway_agent
ROLE_NAME=AmazonBedrockAgentCoreRuntimeRole-${AGENT_NAME}

# trust policy
aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document file://<(envsubst < iam/runtime-trust-policy.json)

# permission policy — note the bedrock-agentcore:InvokeGateway statement scoped
# to the gateway ARN, plus the standard Runtime baseline (X-Ray, log delivery,
# CloudWatch metrics under namespace "bedrock-agentcore", InvokeModel*, etc.)
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name AgentCoreRuntimeInline \
  --policy-document file://<(envsubst < iam/runtime-execution-policy.json)

export EXECUTION_ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" \
  --query 'Role.Arn' --output text)
```

The IAM policies use `${REGION}`, `${ACCOUNT_ID}`, `${AGENT_NAME}`, `${GATEWAY_ID}` placeholders that `envsubst` fills in from the env you sourced.

## Configure + deploy

```bash
agentcore configure \
  --name "$AGENT_NAME" \
  --entrypoint fmkb_gateway_strands.py \
  --execution-role "$EXECUTION_ROLE_ARN" \
  --requirements-file requirements.txt \
  --region "$REGION" \
  --non-interactive

agentcore deploy --agent "$AGENT_NAME" --auto-update-on-conflict \
  -env "GATEWAY_URL=$GATEWAY_URL" \
  -env "AWS_REGION=$REGION" \
  -env "MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0"
```

## Invoke

```bash
agentcore invoke --agent "$AGENT_NAME" \
  '{"prompt":"What does the knowledge base say about cat food?"}'
```

For a programmatic caller (e.g. a Lambda or another agent), use the data-plane API:

```python
import boto3, json
client = boto3.client("bedrock-agentcore", region_name="us-west-2")
resp = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-west-2:<acct>:runtime/<runtime-id>",
    qualifier="DEFAULT",
    runtimeSessionId="some-id-with-at-least-33-chars-...",
    contentType="application/json",
    accept="text/event-stream",
    payload=json.dumps({"prompt": "..."}).encode(),
)
for chunk in resp["response"].iter_chunks():
    print(chunk.decode(), end="")
```

## Local iteration

```bash
agentcore dev --agent "$AGENT_NAME"          # hot-reloading dev server on :8080
agentcore invoke --agent "$AGENT_NAME" --dev '{"prompt":"…"}'

# or run as a local container:
agentcore deploy --local --agent "$AGENT_NAME" -env "GATEWAY_URL=$GATEWAY_URL" …
agentcore invoke --agent "$AGENT_NAME" --local '{"prompt":"…"}'
```

## Cleanup

```bash
agentcore destroy --agent "$AGENT_NAME" --force --delete-ecr-repo
aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name AgentCoreRuntimeInline
aws iam delete-role --role-name "$ROLE_NAME"
```

(Then `python ../01-raw-mcp/cleanup.py` to remove the gateway side.)

## What the agent does

`fmkb_gateway_strands.py` is the Runtime entrypoint. It:

- Builds an MCP client over the gateway URL with `mcp_proxy_for_aws.aws_iam_streamablehttp_client` — every MCP request is SigV4-signed using the runtime execution role.
- Calls `mcp_client.list_tools_sync()` to discover the gateway's KB Retrieve tool.
- Hands those tools to a Strands `Agent`. The agent decides when to call Retrieve.
- Yields **text deltas** (`event["data"]`) back through Runtime's SSE channel — the runtime auto-wraps an async-generator `@app.entrypoint` into `text/event-stream`.

> **Scope note.** The `bedrock-knowledge-bases` connector can also expose
> `AgenticRetrieveStream` as a second tool on the same target. This sample
> wires only `Retrieve` to keep the IAM surface minimal. To enable agentic
> retrieval through the gateway:
>
> 1. Add a second entry to `targetConfiguration.mcp.connector.configurations`
>    with `"name": "AgenticRetrieveStream"`. Its `parameterValues` shape is
>    different from `Retrieve` — it takes `retrievers` (a list of KB
>    references) and a required `agenticRetrieveConfiguration` (foundation
>    model + iteration cap), not a single `knowledgeBaseId`. See the AWS
>    Gateway Quickstart for the full payload.
> 2. Grant **`bedrock:AgenticRetrieveStream` on `*`** to the **gateway role**
>    (not the runtime role). Per the IAM service-authorization reference,
>    this action is not scoped to a knowledge-base resource — granting it on
>    a KB ARN will fail with AccessDenied at tool-call time.

## Tested

This sample was last validated against `bedrock-agentcore` 1.15.0, `strands-agents` 1.44.0, `mcp-proxy-for-aws` 1.6.2, and `mcp` 1.28.0. Newer versions should work; if something breaks, re-pin in `requirements.txt`. To re-validate the inline IAM policy after editing:

```bash
envsubst < iam/runtime-execution-policy.json > /tmp/perm.json
aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY \
  --policy-document file:///tmp/perm.json
```
