# Custom Just-in-Time Auth — a Lambda RESPONSE interceptor that swaps the elicitation URL to the consent portal

> [!NOTE]
> This guide adds a [**Lambda RESPONSE interceptor**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
> to the gateway you built in [`../entra/gateway.md`](../entra/gateway.md) and
> [`../entra/consent-portal.md`](../entra/consent-portal.md). It is the last, optional
> layer of the consent-portal chain — read those two first, and have a **READY** gateway
> and a consent portal before you start here.

![demo](./images/demo-portal.gif)

Once a consent portal is attached to your gateway, a user who invokes a tool can reach the
downstream MCP server through one of two paths.

| Shape | When it fires | What the user sees |
| :--- | :--- | :--- |
| **Upfront auth** | The user consented in the portal *ahead of time* | Nothing — the tool call succeeds silently, because a valid downstream token already exists |
| **Custom Just-in-Time Auth** | First tool call, no prior consent | The **same elicitation, rewritten** to carry the **consent portal URL** instead — so an unconsented user is driven through the same hosted portal as the upfront flow |

**Two URLs, one moment.** When an unconsented user invokes a tool, AgentCore identity mints
an authorization URL and hands it back to the MCP client inside a URL-mode elicitation. Left
alone, that is the **identity URL** — it points straight at the OAuth authorize endpoint. The
**portal URL** points at your hosted [consent portal](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html),
which authenticates the user against your IdP and calls
[`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html)
for you. Custom Just-in-Time Auth intercepts the elicitation on its way out of the gateway
and swaps the first for the second.

## How a RESPONSE interceptor sees the elicitation

An [interceptor](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
is a Lambda the gateway calls on every request (a REQUEST interceptor) or every response (a
RESPONSE interceptor) so you can inspect or transform the payload. A gateway has at most one
of each; ours is RESPONSE-only.

A live Phase-1 run confirmed the real shape (and it is **not** what you might expect): the
just-in-time prompt does **not** arrive as a streaming `elicitation/create` request. It comes
back as a **JSON-RPC error on the `tools/call` response** — a non-streaming response,
`statusCode: 200`, `body.error.code: -32042`:

```json
{
  "interceptorInputVersion": "1.0",
  "mcp": {
    "gatewayResponse": {
      "statusCode": 200,
      "isStreamingResponse": false,
      "body": {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {
          "code": -32042,
          "message": "This request requires more information.",
          "data": {
            "elicitations": [
              {
                "mode": "url",
                "elicitationId": "6c649490-…",
                "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=urn%3Aietf%3Aparams%3Aoauth%3Arequest_uri%3A…",
                "message": "Please login to this URL for authorization."
              }
            ]
          }
        }
      }
    }
  }
}
```

The identity/authorize URL is each entry's `url`, under `body.error.data.elicitations[*].url`.
The interceptor returns a `transformedGatewayResponse` with that URL swapped for the portal
URL:

```json
{
  "interceptorOutputVersion": "1.0",
  "mcp": { "transformedGatewayResponse": { "body": { "...": "..." }, "statusCode": 200 } }
}
```

> [!IMPORTANT]
> The handler includes `statusCode` in its output only when the input event carried one, and
> returns the body unchanged for everything that is not a `-32042` auth-elicitation error.
> That is a pass-through by construction, and it keeps the handler correct if a future flow
> ever does stream (on subsequent streaming events only `body` may be overridden — the status
> code and headers were already sent).

> [!NOTE]
> The interceptor runs with `passRequestHeaders: false`. The rewrite needs only the response
> body, and turning headers off keeps the inbound auth token out of the interceptor event —
> and out of CloudWatch, where Phase 1 logs the whole event.

## Prerequisites

- A **READY** Entra gateway ([`../entra/gateway.md`](../entra/gateway.md)) and a consent
  portal ([`../entra/consent-portal.md`](../entra/consent-portal.md)). `GATEWAY_ID` and
  `PORTAL_URL` are already in `scripts/.env` if you followed those.
- **AWS credentials** able to call `bedrock-agentcore-control`, create a Lambda function and
  an IAM role, and `put_role_policy` on the gateway's service role.
- **`uv`** and Python ≥ 3.12; **boto3/botocore ≥ 1.43.88**. Every command runs from
  `authorization-code-flow/` — the parent of this directory.

Because the exact URL-mode elicitation `params` shape is not in the public docs, this is a
**two-phase** build. Phase 1 deploys a pass-through interceptor that logs the full event so
you can see the real body; Phase 2 flips the *same* Lambda to rewrite the URL, tuned to what
Phase 1 showed you.

## Phase 1 — observe the elicitation body

Deploy the interceptor in its default `log-only` mode. It passes every response through
untouched and logs the full interceptor event to CloudWatch.

```bash
uv run python scripts/deploy_interceptor.py --entra
```

This creates the Lambda (`entra-jit-interceptor`) and its execution role, grants the gateway
role a `lambda:InvokeFunction` scoped to that one function ARN, and calls
[`UpdateGateway`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_UpdateGateway.html)
to wire it as the RESPONSE interceptor.

Now trigger a just-in-time flow:

> [!IMPORTANT]
> Your MCP client (the Inspector, Claude Code, …) redeems the auth code over a back channel
> (no browser `Origin` header), so it is a **public-client / native** app to Entra. Register its
> redirect URIs on your resource app — `$ENTRA_RESOURCE` from
> [`../entra/gateway.md`](../entra/gateway.md) — in the `publicClient.redirectUris` array before
> you start (a different array from the portal's `web` callback; the two coexist), or token
> redemption fails with `AADSTS9002327`:
>
> ```bash
> OBJECT_ID=$(az ad app show --id "$ENTRA_RESOURCE" --query id -o tsv)
> az rest --method PATCH \
>   --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
>   --headers "Content-Type=application/json" \
>   --body '{"publicClient":{"redirectUris":["http://localhost:6274/oauth/callback","http://localhost:6274/oauth/callback/debug","http://localhost:59188/callback"]}}'
> ```
>
> The redirect URI must match your client's callback **exactly**. `6274/oauth/callback` (+
> `/debug`) is the MCP Inspector; `59188/callback` is the port Claude Code used in this demo —
> if yours differs, register the one your client actually redirects to. The PATCH **replaces**
> the whole `publicClient.redirectUris` array, so list every URI in one call.

1. Using an MCP client (the AgentCore gateway MCP Inspector, or Claude Code), sign in as a user
   who has **not** consented in the portal and invoke any tool on the MCP server.
2. Open the Lambda's CloudWatch log group `/aws/lambda/entra-jit-interceptor` and find the
   line beginning `INTERCEPTOR_EVENT`. That is the full, parsed event.
3. In `mcp.gatewayResponse.body`, confirm `error.code` is `-32042` and locate the AgentCore
   identity URL at `error.data.elicitations[*].url` (the shape shown above).

> [!TIP]
> The handler's `rewrite_elicitation_urls` targets the confirmed field
> `body.error.data.elicitations[*].url` and only swaps entries whose `url` host contains
> `bedrock-agentcore` (`IDENTITY_URL_HOST`). This was pinned from the Phase-1 log above; see
> [`scripts/lambda/interceptor.py`](../scripts/lambda/interceptor.py).

> [!NOTE]
> **Session context is handled by the portal, not the URL.** The identity URL carries a
> `request_uri` (`urn:ietf:params:oauth:request_uri:…`) and each elicitation an `elicitationId`,
> but you do **not** need to carry either onto the portal URL. The hosted portal correlates the
> pending request from the authenticated end-user session and completes
> `CompleteResourceTokenAuth` itself, so a bare `PORTAL_URL` is sufficient — which is exactly
> what the handler injects.

## Phase 2 — rewrite the URL to the portal

Flip the same Lambda to `rewrite`. `PORTAL_URL` must be in `.env` (it is, if you ran
`deploy_portal.py --entra`).

```bash
uv run python scripts/deploy_interceptor.py --entra --mode rewrite
```

Re-invoke a tool as an unconsented user. The MCP client should now be handed the **portal
URL** in the elicitation instead of the raw identity URL, and the user completes consent
through the hosted portal exactly as an upfront user would. The Lambda logs how many URLs it
rewrote on each `-32042` auth-elicitation error.

## Cleanup

```bash
uv run python scripts/cleanup_interceptor.py --entra
```

This detaches the interceptor (`UpdateGateway` with an empty `interceptorConfigurations`),
deletes the Lambda and its role, and removes the `InterceptorInvoke` inline policy from the
gateway role. It **does not** touch the gateway, the portal, any target, or the Entra
resource app — tear those down with their own cleanup scripts.

> [!NOTE]
> `scripts/invoke.py` does **not** work against this gateway — it mints its inbound token
> from the Cognito stack, and the Entra gateway's authorizer will not accept it. Use the
> MCP Inspector to drive the flows above.

---

**No live AWS call has been made from this directory.** The Lambda deploy, the `UpdateGateway`
interceptor wiring, and the live elicitation body are unverified against an account —
Phase 1's run is yours to make.
