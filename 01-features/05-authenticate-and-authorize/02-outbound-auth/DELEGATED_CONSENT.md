# Confirm a delegated user grant without deploying an agent

A single-user desktop client asks AgentCore Identity for a user-delegated token for **Google Calendar** or **GitHub**. It demonstrates a cached grant and an interactive authorization-code flow. It verifies that a token was returned; it never prints the token, sends it to a model, or reads calendar events or private repositories. This is not a hosted or multi-user callback server.

## Prerequisites (owner-managed)

Use a sandbox with an existing workload identity and an existing OAuth provider configured for your Google or GitHub application. Register the Identity-issued callback URL with that provider. Separately, allow `http://127.0.0.1:9090/callback` as the **application return URL** on the workload identity. Don't replace a shared identity's return URL list. Grant only `calendar.readonly` for Google or `read:user` for GitHub (the sample does not request `repo`). Use an inbound user JWT from your trusted login flow. The AWS caller must be authorized to exchange that JWT for this workload and use `GetResourceOauth2Token` and `CompleteResourceTokenAuth` for these resources. Do not use a user ID string as proof of identity.

From `02-outbound-auth/` with Python 3.12 and a local desktop browser:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install 'boto3==1.44.9'
python -m unittest -v test_delegated_consent  # offline; uses a loopback callback fixture
python delegated_consent.py calendar --account "$ACCOUNT" --region "$AWS_REGION" \
  --workload "$WORKLOAD_NAME" --provider "$GOOGLE_PROVIDER_NAME"
# Or use: github ... --provider "$GITHUB_PROVIDER_NAME"
```

The second command after the offline test is a **live** operation. Use a short-lived AWS profile and enter the user JWT only at the hidden terminal prompt. No JWT, callback URL or grant is written to a receipt or log. When consent is required, the client opens the AWS-provided HTTPS URL in your local browser. The loopback listener is created before opening the browser; it accepts only the matching session ID and the opaque state originally sent in `customState`. Missing/mismatched values do not complete the flow. The listener closes after a match or three minutes. The same original user JWT is supplied when completing that session. See [Identity session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html).

A desktop user must own the browser, terminal and local machine. Don't tunnel the listener, run it in a shared workshop server or forward a returned consent URL. If your IdP, return-URL policy or browser doesn't support this loopback flow, stop rather than bypassing the session/state check. A web application needs its own authenticated session and CSRF protection or a managed consent flow. See [managed consent for GitHub MCP](../07-consent-portal-auth-code-flow-targets/).

No workload identity, provider, IAM role, Cognito account, Runtime or stack is created or deleted. Consent **does** affect the user's authorization and can remain in the token vault. Revoke the application's grant at the provider and follow your organization's token-vault cleanup procedure when finished. Leave shared identity infrastructure alone.
