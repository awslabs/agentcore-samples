# Agent — deployed on AgentCore Runtime

A minimal Strands agent that performs OBO inside its request handler.

## What it does

- Accepts POST requests at the Runtime invoke URL.
- AgentCore Runtime validates the inbound JWT using the configured customJWTAuthorizer (Entra OIDC discovery).
- The handler reads the user JWT from the request context.
- The `get_my_profile` tool performs the OBO exchange via AgentCore Identity and calls Microsoft Graph `/me`.
- The LLM composes a short natural-language answer from the returned profile.

## Key design points

- **The LLM never sees the OBO'd Graph token.** The tool wraps "OBO + Graph call" as one operation, returning only the profile JSON.
- **The user JWT is passed to the tool, not held by the LLM.** In the system prompt the LLM is instructed to call the tool with the JWT; in the tool, the token is used exactly once and not retained.
- **No secrets in the handler.** The client secret for the Entra agent app lives in the AgentCore credential provider, not in the agent's env or code.

## How it's deployed

Deployment uses the Node.js AgentCore CLI (`@aws/agentcore`), not this folder directly. The workflow (spelled out in `real-world/README.md`):

1. `agentcore create --name "$AGENT_RUNTIME_NAME" --framework Strands --model-provider Bedrock --memory none --build CodeZip --defaults` scaffolds a CLI project at `real-world/$AGENT_RUNTIME_NAME/`.
2. You copy `agent/agent.py` over the scaffold's generated `app/$AGENT_RUNTIME_NAME/main.py`.
3. `python ../deploy/02_patch_agentcore_json.py` patches `agentcore/agentcore.json` with the Entra `CUSTOM_JWT` inbound auth config and the environment variables the agent reads.
4. `agentcore validate && agentcore deploy -y -v` builds the zip, pushes to ECR via the CDK-managed stack, and creates/updates the Runtime.
5. `python ../deploy/03_grant_agent_iam_permissions.py` attaches the OBO IAM policy to the auto-created execution role.
6. `agentcore status` prints the Runtime ARN — paste the derived invoke URL into `real-world/.env` as `AGENT_RUNTIME_INVOKE_URL`.

## Running locally (for quick iteration without deploying)

```bash
cd agent
pip install -r requirements.txt
python agent.py
# listens on http://localhost:8080
```

Then test with:
```bash
curl -X POST http://localhost:8080/invocations \
  -H "Authorization: Bearer <user-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is my email?"}'
```

Note: without the Runtime wrapper, inbound JWT validation is not enforced — the handler just reads whatever bearer you send. This is fine for local iteration but do NOT expose this local port publicly.
