// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Tabs from "@cloudscape-design/components/tabs";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import Link from "@cloudscape-design/components/link";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import type { RegistryAuthorizerType } from "@aws-sdk/client-agent-registry-control";
import {
  config,
  cognitoDiscoveryUrl,
  registryMcpEndpoint,
  registryMcpMetadataUrl,
} from "../config";

const DOC_MCP_ENDPOINT =
  "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-mcp-endpoint.html";
const DOC_AUTH_TYPES =
  "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-supported-auth-types.html";
const DOC_QUICK_MCP =
  "https://docs.aws.amazon.com/quick/latest/userguide/mcp-integration.html";
const DOC_KIRO_MCP = "https://kiro.dev/docs/mcp/configuration/";

interface Props {
  registryId: string;
  /** From GetRegistry — undefined while loading. Falls back to the build-time mode. */
  authorizerType?: RegistryAuthorizerType | string;
}

/** A labelled, copyable code block. */
function Snippet({ label, code }: { label: string; code: string }) {
  return (
    <SpaceBetween size="xxs">
      <Box variant="awsui-key-label">{label}</Box>
      <CopyToClipboard
        copyButtonText="Copy"
        copyErrorText="Failed to copy"
        copySuccessText="Copied"
        textToCopy={code}
        variant="button"
      />
      <Box variant="code">
        <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{code}</pre>
      </Box>
    </SpaceBetween>
  );
}

/**
 * "Connect your tools" — how to consume THIS registry from an MCP client.
 *
 * Everything here is derived from live values (region, registry id, user pool,
 * app client), and the endpoint shown is the same one this app itself uses for
 * discovery when the registry is CUSTOM_JWT — so the instructions are exercised
 * rather than merely asserted.
 */
export default function ConnectRegistry({ registryId, authorizerType }: Props) {
  const mode = authorizerType ?? config.registryAuthMode;
  const isJwt = mode === "CUSTOM_JWT";
  const endpoint = registryMcpEndpoint(registryId);

  const tokenSnippet = `# Mint a Cognito ACCESS token. The registry's CUSTOM_JWT authorizer trusts this
# sample's user pool, and matches the token's client_id claim against its
# allowedClients list. (Add SECRET_HASH if your app client has a secret.)
aws cognito-idp initiate-auth \\
  --region ${config.region} \\
  --client-id ${config.userPoolClientId} \\
  --auth-flow USER_PASSWORD_AUTH \\
  --auth-parameters USERNAME=consumer@example.com,PASSWORD='<password>' \\
  --query 'AuthenticationResult.AccessToken' --output text`;

  const kiroSnippet = `// ~/.kiro/settings/mcp.json  (global)  or  .kiro/settings/mcp.json  (workspace)
{
  "mcpServers": {
    "agent-registry": {
      "url": "${endpoint}",
      "headers": {
        "Authorization": "Bearer \${AGENT_REGISTRY_TOKEN}"
      }
    }
  }
}`;

  const genericSnippet = `{
  "mcpServers": {
    "agent-registry": {
      "type": "http",
      "url": "${endpoint}",
      "headers": {
        "Authorization": "Bearer \${AGENT_REGISTRY_TOKEN}"
      }
    }
  }
}`;

  const curlJwt = `TOKEN=<paste the access token>

curl -s -X POST "${endpoint}" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"search_discoverable_registry_records",
                 "arguments":{"searchQuery":"restart unhealthy tasks"}}}'`;

  const curlIam = `# AWS_IAM registry: sign the same request with SigV4 instead of a bearer token.
curl -s -X POST "${endpoint}" \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  -H "X-Amz-Security-Token: $AWS_SESSION_TOKEN" \\
  --aws-sigv4 "aws:amz:${config.region}:agent-registry" \\
  --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"search_discoverable_registry_records",
                 "arguments":{"searchQuery":"restart unhealthy tasks"}}}'`;

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Point an MCP client at this registry so an agent can discover its approved records at runtime."
        >
          Connect your tools
        </Header>
      }
    >
      <SpaceBetween size="l">
        <KeyValuePairs
          columns={2}
          items={[
            {
              label: "MCP endpoint",
              value: (
                <CopyToClipboard
                  copyButtonAriaLabel="Copy MCP endpoint"
                  copyErrorText="Failed to copy"
                  copySuccessText="Copied"
                  textToCopy={endpoint}
                  variant="inline"
                />
              ),
            },
            { label: "Inbound authorization", value: String(mode) },
            {
              label: "Tools exposed",
              value:
                "search_discoverable_registry_records, list_discoverable_registry_records, batch_get_discoverable_registry_record",
            },
            {
              label: "Transport",
              value: "Streamable HTTP (JSON-RPC, MCP 2025-11-25)",
            },
          ]}
        />

        {isJwt ? (
          <Alert
            type="info"
            header="This registry authorizes consumers with a Cognito JWT"
          >
            Discovery calls carry{" "}
            <Box variant="code">Authorization: Bearer &lt;token&gt;</Box> from
            this sample's Cognito user pool — the authorizer's discovery URL is{" "}
            <Box variant="code">{cognitoDiscoveryUrl()}</Box> and its allowed
            client is <Box variant="code">{config.userPoolClientId}</Box>.
            Control-plane operations (publishing, approving) always use IAM
            regardless.{" "}
            <Link external href={DOC_AUTH_TYPES}>
              Supported inbound authorization types
            </Link>
          </Alert>
        ) : (
          <Alert
            type="info"
            header="This registry authorizes consumers with AWS IAM"
          >
            Discovery calls are SigV4-signed with the caller's IAM role. A
            stdio-only MCP client needs an MCP proxy that can sign requests; a
            JWT registry is simpler to hand to an external tool.{" "}
            <Link external href={DOC_AUTH_TYPES}>
              Supported inbound authorization types
            </Link>
          </Alert>
        )}

        <Tabs
          tabs={[
            ...(isJwt
              ? [
                  {
                    id: "token",
                    label: "1. Get a token",
                    content: (
                      <SpaceBetween size="m">
                        <Box variant="p">
                          Every client below needs a Cognito access token.
                          Tokens are short-lived — for an unattended agent, use
                          a machine-to-machine app client with the{" "}
                          <Box variant="code">client_credentials</Box> grant and
                          let the client refresh it.
                        </Box>
                        <Snippet label="AWS CLI" code={tokenSnippet} />
                      </SpaceBetween>
                    ),
                  },
                ]
              : []),
            {
              id: "kiro",
              label: "Kiro",
              content: (
                <SpaceBetween size="m">
                  <Box variant="p">
                    Add the registry as a remote MCP server. Kiro reloads MCP
                    config on save; if the server does not connect, run{" "}
                    <Box variant="code">Developer: Reload Window</Box>.
                    Referenced environment variables must be allow-listed under
                    Settings → <Box variant="code">Mcp Approved Env Vars</Box>.
                  </Box>
                  <Snippet
                    label="mcp.json"
                    code={isJwt ? kiroSnippet : genericSnippet}
                  />
                  <Link external href={DOC_KIRO_MCP}>
                    Kiro MCP configuration reference
                  </Link>
                </SpaceBetween>
              ),
            },
            {
              id: "quick",
              label: "Amazon Quick",
              content: (
                <SpaceBetween size="m">
                  <Box variant="p">
                    Amazon Quick has a built-in MCP client configured in the
                    console — there is no local config file:
                  </Box>
                  <Box variant="p">
                    <b>Connectors</b> → <b>Create for your team</b> →{" "}
                    <b>Model Context Protocol (MCP)</b> → set <b>Name</b>, paste
                    the <b>MCP server endpoint</b> above, choose{" "}
                    <b>Connection type</b> (Public network, or a VPC connection)
                    → <b>Next</b> → choose the authentication method →{" "}
                    <b>Create and continue</b>.
                  </Box>
                  <Alert
                    type="warning"
                    header="Use OAuth, not a static bearer header"
                  >
                    Quick does not support custom HTTP headers, so the{" "}
                    <Box variant="code">Authorization: Bearer</Box> approach
                    used by Kiro does not apply. Choose{" "}
                    <b>Service authentication</b> and supply a Cognito
                    machine-to-machine app client's <b>Client ID</b>,{" "}
                    <b>Client Secret</b> and <b>Token URL</b> (
                    <Box variant="code">
                      https://&lt;your-domain&gt;.auth.{config.region}
                      .amazoncognito.com/oauth2/token
                    </Box>
                    ). That app client must be in the registry's allowed clients
                    list.
                  </Alert>
                  <Link external href={DOC_QUICK_MCP}>
                    Amazon Quick MCP integration
                  </Link>
                </SpaceBetween>
              ),
            },
            {
              id: "generic",
              label: "Claude & generic MCP",
              content: (
                <SpaceBetween size="m">
                  <Box variant="p">
                    Any MCP client that speaks streamable HTTP can use the same
                    endpoint. Clients that discover auth dynamically can read
                    the protected-resource metadata at{" "}
                    <Box variant="code">
                      {registryMcpMetadataUrl(registryId)}
                    </Box>
                    .
                  </Box>
                  <Snippet label="mcp.json" code={genericSnippet} />
                </SpaceBetween>
              ),
            },
            {
              id: "curl",
              label: "curl",
              content: (
                <SpaceBetween size="m">
                  <Box variant="p">
                    The endpoint is stateless for{" "}
                    <Box variant="code">tools/call</Box>, so a single JSON-RPC
                    POST is enough — no <Box variant="code">initialize</Box>{" "}
                    handshake. Useful for verifying the token and the registry's
                    authorizer before wiring up a client.
                  </Box>
                  <Snippet label="Request" code={isJwt ? curlJwt : curlIam} />
                  <Link external href={DOC_MCP_ENDPOINT}>
                    Using the registry MCP endpoint
                  </Link>
                </SpaceBetween>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}
