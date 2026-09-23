# IAM-scoped actor isolation

Restrict memory access at the IAM layer so a runtime execution role can only touch one user's data. The agent extracts the authenticated user's Cognito `sub` from the JWT, sets it as `actorId` on every memory call, and an IAM condition on `bedrock-agentcore:actorId` enforces that only that actor's events are reachable.

## What you learn

- Build an IAM policy that conditions memory actions on `bedrock-agentcore:actorId` (and optionally `sessionId` / `namespace` / `namespacePath`)
- Wire a Cognito JWT authorizer into AgentCore Runtime
- Propagate the authenticated user's `sub` into the agent so the IAM condition matches at runtime
- Verify isolation by attempting cross-user access and observing an `AccessDeniedException`

## Architecture

![IAM-scoped actor isolation](./architecture.png)

The runtime endpoint sits behind a Cognito JWT authorizer. The runtime execution role has an inline policy with `Condition: { "StringEquals": { "bedrock-agentcore:actorId": "<sub>" } }`. Even if the agent code is buggy and passes the wrong `actorId`, the call fails IAM evaluation.

## Run

```bash
pip install -r requirements.txt
python runtime_memory_identity_integration.py
```

The script provisions Cognito, creates the memory resource, deploys the agent with the IAM-scoped role, and invokes the endpoint with two different users to demonstrate the boundary.

## Available IAM condition keys

| Key | Scopes |
|---|---|
| `bedrock-agentcore:actorId` | Restrict to one or more actors |
| `bedrock-agentcore:sessionId` | Restrict to one session (rare) |
| `bedrock-agentcore:namespace` | Exact namespace match |
| `bedrock-agentcore:namespacePath` | Hierarchical namespace match |

## Best practices

- **Treat IAM as the authoritative boundary.** Application-layer `actorId` checks are best-effort; IAM is what survives a code bug.
- **Pair with a stable `actorId`.** Cognito `sub` is the right choice — immutable and cryptographically tied to the authenticated principal.
- **Combine `actorId` + `namespacePath`** for fine-grained policies — a single role can be allowed to read facts but not preferences for one user.
- **Audit `bedrock-agentcore:actorId` in CloudTrail.** Cross-user access attempts surface as denials with the offending `actorId`.
- **Don't put the `actorId` in the policy resource ARN.** The condition key is the right hook — ARNs don't address individual actors.

## Where to go next

- Per-user temporary credentials via Cognito Identity Pools (no shared service role): [`../02-cognito-federated-identity/`](../02-cognito-federated-identity/)
- Customer-managed encryption keys for tenant isolation: [`../03-kms-encryption/`](../03-kms-encryption/)
- Namespace organisation that pairs with these IAM conditions: [`../../02-long-term-memory/04-namespaces/`](../../02-long-term-memory/04-namespaces/)
