# Entra ID On-Behalf-Of for an MCP tool on AgentCore Runtime

## Introduction

A Strands agent running on Amazon Bedrock AgentCore Runtime calls Microsoft Graph on the signed-in user's behalf, via an MCP server deployed as a separate AgentCore Runtime. The agent exchanges the inbound user JWT for a Graph-scoped delegation token using the Microsoft Entra ID On-Behalf-Of (OBO) flow, and forwards that token to the MCP server in a custom request header. The delegation token carries `sub=user, act=agent` per RFC 8693, so Graph audit logs record "agent acting on behalf of user". The user JWT never crosses the agent to MCP boundary; the LLM never sees any token.

This sample covers the Runtime-hosted MCP variant of OBO. The Gateway variant, which performs the OBO exchange inside AgentCore Gateway without agent-side code, is covered in `06-workshops/02-AgentCore-gateway/18-Outbound_Auth_OBO_Microsoft/`.

## Architecture

![Architecture](./images/architecture.png)

## What the sample demonstrates

- Inbound auth on an AgentCore Runtime using a Microsoft Entra ID JWT (`customJWTAuthorizer`).
- Outbound M2M auth from the agent to the MCP server using a client-credentials token against a second Entra app registration.
- Outbound OBO auth from the agent to Microsoft Graph using `GetResourceOauth2Token` with `oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE` on AgentCore Identity.
- Forwarding the Graph-scoped delegation token to the MCP server in the `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Graph-Token` request header. Requires `requestHeaderConfiguration.requestHeaderAllowlist` on the MCP runtime so the custom header survives the request hop.

## Tutorial Details

| Information       | Details                                                           |
|:------------------|:------------------------------------------------------------------|
| Tutorial type     | Jupyter notebook                                                  |
| Agent framework   | Strands Agents                                                    |
| LLM model         | Anthropic Claude Sonnet 4.5                                       |
| Inbound Auth      | Microsoft Entra ID (`CUSTOM_JWT`)                                 |
| Outbound Auth     | OBO (RFC 8693) to Microsoft Graph + M2M to MCP Runtime            |
| AgentCore surface | Runtime (two runtimes: one HTTP agent, one MCP server)            |
| CLI tool          | `bedrock-agentcore-starter-toolkit` (Python)                      |
| Complexity        | Advanced                                                          |

## Prerequisites

- AWS account with Bedrock AgentCore access. The notebook defaults to `us-west-2`.
- Microsoft Entra ID tenant with admin rights to register applications and grant admin consent.
- Python 3.11+ with the Jupyter stack (`ipykernel`, `jupyter`).
- AWS credentials available to the notebook kernel (for example, `AWS_PROFILE` exported before launching Jupyter).
- Model access to Claude Sonnet 4.5 on Amazon Bedrock.

The notebook has a Prerequisites section that walks through creating the two Entra app registrations (agent app and MCP server app), exposing scopes, declaring app roles, and granting admin consent. Follow that section before running any code cells.

## Usage

1. Create a virtual environment and install the notebook-host dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Register the kernel:

   ```bash
   python -m ipykernel install --user --name=agentcore-entra-obo \
       --display-name="Python (agentcore-entra-obo)"
   ```

3. Open `runtime_with_entra_id_obo_and_mcp.ipynb`, select the kernel above, and run cells top to bottom.

## Sample Prompts

Once both runtimes are deployed and the agent is invoked with a valid user JWT, the agent calls the `get_my_profile` MCP tool against Microsoft Graph `/me` and answers from the returned JSON. The Graph token is scoped to `User.Read`, so only the signed-in user's own profile is readable.

- "What is my email address?"
- "What is my display name?"
- "What is my job title?"
- "Give me a summary of my Microsoft 365 profile."

## Files

- `runtime_with_entra_id_obo_and_mcp.ipynb`: the full walkthrough.
- `requirements.txt`: dependencies for running the notebook locally.
- `mcp/requirements.txt`, `agent/requirements.txt`: dependencies for the two Runtime container builds. The notebook deploys each Runtime from its own subdirectory so the starter toolkit's generated `Dockerfile` and `.bedrock_agentcore.yaml` don't collide.
- `images/`: architecture diagram (PNG).

## Clean Up

The notebook's Cleanup section deletes both runtimes and the credential provider created by the walkthrough. The Entra app registrations are not deleted by the notebook; remove them from the Entra admin center if you no longer need them.

## Related reading

- [AgentCore OBO token exchange](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)
- [Pass custom headers to AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html)
- [Microsoft Entra ID On-Behalf-Of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [RFC 8693: OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693)
