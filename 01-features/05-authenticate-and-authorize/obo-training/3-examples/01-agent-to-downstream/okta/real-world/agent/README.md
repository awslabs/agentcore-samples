# Agent — deployed on AgentCore Runtime (Okta flavor)

A minimal Strands agent that performs the Okta OBO exchange inside its request handler.

## What it does

- Accepts POST requests at the Runtime invoke URL.
- AgentCore Runtime validates the inbound JWT using the configured `customJWTAuthorizer` (Okta OIDC discovery).
- The handler reads the user JWT from the request context.
- The `get_my_profile` tool:
  1. Performs the **OBO exchange** via AgentCore Identity (RFC 8693) for a custom-scope downstream token (`agent.downstream`).
  2. Calls Okta's **`/v1/userinfo`** endpoint with the **inbound user token** to fetch profile fields.
  3. Returns both the profile JSON and a small set of claims from the OBO'd token as `obo_proof` (so you can verify the exchange happened).
- The LLM composes a short natural-language answer from the profile.

## Key design points

- **Why two independent calls?** Okta's Token Exchange grant refuses `openid`, and Okta's OIDC spec refuses `profile`/`email` without `openid`. So an OBO token cannot carry any OIDC scope — which means it can't be used against `/v1/userinfo`. The realistic production pattern is to use the OBO token against your *own* resource server (which accepts the custom scope). In this example we demonstrate the OBO mechanics with `agent.downstream`, and use the inbound user token for the userinfo call so the UI has something to display.
- **The LLM never sees either token.** The tool returns parsed profile JSON plus an `obo_proof` dict of claims; tokens stay in the tool's local scope.
- **The user JWT is passed to the tool, not held by the LLM.** The system prompt instructs the LLM to call the tool; inside the tool the token is used exactly once (for the OBO exchange and for the userinfo call) and not retained.
- **No secrets in the handler.** The Service App client secret lives on the AgentCore credential provider, not in the agent's env or code.
- **Okta-specific exchange parameters are in one place.** `_obo_exchange()` is the single spot where `customParameters={"subject_token_type": ...}` and `audiences=[OKTA_AUDIENCE]` are passed. The rest of the agent is IdP-agnostic.

## How it's deployed

Deployment uses the Node.js AgentCore CLI (`@aws/agentcore`), not this folder directly. The workflow (full detail in `../README.md`):

1. `agentcore create --name "$AGENT_RUNTIME_NAME" --framework Strands --model-provider Bedrock --memory none --build CodeZip --defaults` scaffolds a project at `real-world/$AGENT_RUNTIME_NAME/`.
2. Copy `agent/agent.py` over the scaffold's generated `app/$AGENT_RUNTIME_NAME/main.py`.
3. `python ../deploy/02_patch_agentcore_json.py` patches `agentcore/agentcore.json` with the Okta `CUSTOM_JWT` inbound auth config and the environment variables the agent reads.
4. `agentcore validate && agentcore deploy -y -v` builds the zip, pushes to ECR via a CDK-managed stack, and creates/updates the Runtime.
5. `python ../deploy/03_grant_agent_iam_permissions.py` attaches the OBO IAM policy to the auto-created execution role.
6. `agentcore status` prints the Runtime ARN — the derived invoke URL goes into `real-world/.env` as `AGENT_RUNTIME_INVOKE_URL`.

## Running locally (for quick iteration without deploying)

```bash
cd agent
pip install -r requirements.txt
python agent.py
# listens on http://localhost:8080
```

Test:

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Authorization: Bearer <user-jwt-from-okta>" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is my email?"}'
```

Without the Runtime wrapper, inbound JWT validation is not enforced — the handler reads whatever bearer you send. Fine for local iteration; do NOT expose this port publicly.
