# Inbound Authentication

Protect your AgentCore runtime and gateway endpoints so that only callers with valid credentials
can reach your agents.

## Top-level layout

| Folder | identity Provider | What's inside |
|:-------|:-----------------|:--------------|
| `01-inbound-auth-cognito/` | Amazon Cognito | runtime protected by Cognito JWT; tests unauthenticated rejection + authenticated invocation |
| `02-inbound-auth-EntraID/` | Microsoft Entra ID | Three patterns: runtime inbound auth, gateway M2M, gateway with 3LO Auth Code (OneNote) |
| `03-inbound-auth-okta/` | Okta | Two patterns: runtime inbound auth with scope validation, gateway with full OAuth flow |
| `04-inbound-auth-pingfederate/` | PingFederate | Full CDK deployment: VPC, VPN, PingFederate on EC2, AgentCore gateway with custom JWT |

## How this section is organized

Each sub-folder focuses on a single identity provider and contains self-contained Python scripts
(or a CDK app for PingFederate). The scripts demonstrate how to:

1. Create an AgentCore runtime or gateway with `authorizerType="CUSTOM_JWT"`
2. Configure `discoveryUrl`, `allowedAudience`, and `allowedClients` for the provider
3. Acquire a bearer token from the IdP and invoke the protected endpoint
4. Verify that unauthenticated requests are rejected (HTTP 401/403)

## Concepts

| Concept | Details |
|:--------|:--------|
| `customJWTAuthorizer` | AgentCore's built-in JWT verifier; validates signature, expiry, audience, and client claims against the IdP's JWKS endpoint |
| `discoveryUrl` | OIDC well-known configuration URL; AgentCore fetches the JWKS URI from this endpoint |
| `allowedAudience` | List of acceptable `aud` claim values in the incoming JWT |
| `allowedClients` | List of acceptable `client_id` / `azp` values |
| `customClaims` | Additional claim assertions (e.g. Entra app roles) evaluated after signature validation |

## Prerequisites

- Python 3.10+
- AWS CLI configured with credentials
- `bedrock-agentcore` package: `pip install bedrock-agentcore`
- An account at the relevant identity provider (Cognito, Entra ID, Okta, or PingFederate)

## Running the Python Scripts

### 01-inbound-auth-cognito

```bash
cd 01-inbound-auth-cognito/
pip install -r requirements.txt
python inbound_auth_runtime.py
# Cleanup:
python inbound_auth_runtime.py --cleanup
```

### 02-inbound-auth-EntraID

```bash
cd 02-inbound-auth-EntraID/
pip install -r requirements.txt

# Tutorial 1 — runtime inbound auth
python entra_id_inbound_auth.py

# Tutorial 2 — gateway M2M
python entra_gateway_m2m.py

# Tutorial 3 — gateway Auth Code Flow (OneNote)
python entra_gateway_auth_code.py
```

### 03-inbound-auth-okta

```bash
cd 03-inbound-auth-okta/
pip install -r requirements.txt

# Tutorial 1 — runtime inbound auth with scope validation
python okta_inbound_auth.py

# Tutorial 2 — gateway with full OAuth flow
python okta_gateway_auth.py
```

### 04-inbound-auth-pingfederate

```bash
cd 04-inbound-auth-pingfederate/
pip install -r requirements.txt   # CDK dependencies
./deploy_sample.sh                 # Full CDK deploy
# Cleanup:
./cleanup_sample.sh
```

## Resources

- [AgentCore identity documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html)
- [Configuring inbound auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-auth.html)
- [OIDC discovery](https://openid.net/specs/openid-connect-discovery-1_0.html)
