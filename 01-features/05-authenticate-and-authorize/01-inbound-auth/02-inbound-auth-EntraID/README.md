# Verify Entra ID inbound authentication on an existing Runtime

Test the difference between **an authenticated caller** and a Runtime that merely returns a response. This single-user check inspects the configured JWT authorizer, tries a synthetic prompt without a bearer, and sends an Entra access token only if that request is denied. It never creates a role, changes an authorizer or deletes a Runtime.

Bring an owner-approved, IAM-control-plane-readable sandbox Runtime protected by Microsoft Entra ID. Its discovery URL must be specific to the intended Entra tenant (v1 or v2) and `allowedAudience` must include the expected resource identifier. Obtain a short-lived access JWT from the normal login flow for that audience. The AWS profile needs STS identity lookup and `GetAgentRuntime`; the Entra token, not the AWS credentials, authorizes the data-plane request. Use a Runtime that accepts `{"prompt":"Reply with OK"}` and has no destructive tools: the authenticated request really invokes it, and even an unexpectedly accepted unauthenticated probe could incur charges.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v test_inbound
python check_inbound.py --region "$AWS_REGION" --account "$ACCOUNT" --runtime-id "$RUNTIME_ID" \
  --tenant-id "$ENTRA_TENANT_ID" --audience "$ENTRA_AUDIENCE"
```

Enter the bearer only at the hidden prompt. The program derives the HTTPS destination from the verified ARN and Region, does not follow redirects, never decodes a JWT as proof of identity, and does not print the response content. It requires 401/403 for the first request and HTTP 200 for the second. A 200 establishes neither answer quality nor fine-grained authorization of tools. No cleanup is needed for AWS infrastructure; logs, usage and token expiry remain subject to their owners' policies.

The [previous walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/05-authenticate-and-authorize/01-inbound-auth/02-inbound-auth-EntraID) also provisioned Gateway M2M and a OneNote agent. This check does not reproduce those deployments. For Microsoft app setup and delegated flows see [Entra certificate-based authentication](../../05-certificate-based-auth/entra/) and [managed consent](../../07-consent-portal-auth-code-flow-targets/). Reconcile historical resource IDs before cleanup: the previous helper could reuse IAM roles and then remove policies or delete a bucket's contents by name.
