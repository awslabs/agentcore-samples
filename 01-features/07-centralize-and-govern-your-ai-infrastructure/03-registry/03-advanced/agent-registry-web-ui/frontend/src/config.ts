// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * How the registry authorizes CONSUMER (data-plane) access.
 *
 * A registry's `discoveryConfiguration.authorizerType` is set at creation and is
 * immutable. It controls only the data-plane / discovery surface —
 * SearchDiscoverableRegistryRecords, ListDiscoverableRegistryRecords,
 * BatchGetDiscoverableRegistryRecord and InvokeRegistryMcp. Every control-plane
 * operation (list/create/update records, approve, reject) always requires IAM
 * regardless of this setting.
 *
 *  - CUSTOM_JWT : consumers present `Authorization: Bearer <OIDC JWT>`. This sample
 *                 points the authorizer at its own Cognito user pool, so the very
 *                 same token the SPA holds is what an external MCP client (Kiro,
 *                 Amazon Quick, Claude) sends. Discovery goes through the registry's
 *                 MCP endpoint.
 *  - AWS_IAM    : consumers sign with SigV4 using the persona-scoped IAM role.
 *                 Discovery goes through the data-plane SDK client.
 */
export type RegistryAuthMode = "CUSTOM_JWT" | "AWS_IAM";

function authMode(): RegistryAuthMode {
  return import.meta.env.VITE_REGISTRY_AUTH_MODE === "AWS_IAM"
    ? "AWS_IAM"
    : "CUSTOM_JWT";
}

export const config = {
  region: import.meta.env.VITE_AWS_REGION,
  userPoolId: import.meta.env.VITE_USER_POOL_ID,
  userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID,
  identityPoolId: import.meta.env.VITE_IDENTITY_POOL_ID,
  registryId: import.meta.env.VITE_REGISTRY_ID,
  registryAuthMode: authMode(),
  /**
   * ARN of the AgentCore Identity OAuth2 credential provider this sample deploys
   * (see deploy/setup/06-sample-mcp-gateway.sh). It is configured against the SAME
   * Cognito user pool as the app, so a record that synchronizes from a URL can get a
   * machine-to-machine token for that endpoint with no extra setup. Optional: when
   * unset, the record wizard still offers IAM SigV4 or an unauthenticated source.
   */
  oauthCredentialProviderArn: import.meta.env
    .VITE_OAUTH_CREDENTIAL_PROVIDER_ARN,
  /** Default scopes requested with the client_credentials grant (space separated). */
  oauthCredentialProviderScopes: import.meta.env
    .VITE_OAUTH_CREDENTIAL_PROVIDER_SCOPES,
  /** MCP endpoint of the sample AgentCore Gateway, prefilled as the source URL. */
  sampleGatewayUrl: import.meta.env.VITE_SAMPLE_GATEWAY_URL,
};

export const cognitoLoginKey = `cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`;

/**
 * The OpenID Connect discovery document of the sample's own Cognito user pool.
 * This is the `discoveryUrl` the registry's CUSTOM_JWT authorizer is created with,
 * and the value an operator needs when wiring a client's OAuth flow.
 */
export function cognitoDiscoveryUrl(): string {
  return `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}/.well-known/openid-configuration`;
}

/**
 * The registry's MCP endpoint. Any MCP client (Kiro, Amazon Quick, Claude, curl)
 * talks to this URL; under CUSTOM_JWT it is authorized with a Cognito bearer token.
 */
export function registryMcpEndpoint(registryId: string): string {
  return `https://agent-registry.${config.region}.api.aws/registry/${registryId}/mcp`;
}

/** RFC 9728 protected-resource metadata for the registry's MCP endpoint. */
export function registryMcpMetadataUrl(registryId: string): string {
  return `https://agent-registry.${config.region}.api.aws/.well-known/oauth-protected-resource/registry/${registryId}/mcp`;
}

export function assertConfig(): string[] {
  const missing: string[] = [];
  if (!config.region) missing.push("VITE_AWS_REGION");
  if (!config.userPoolId) missing.push("VITE_USER_POOL_ID");
  if (!config.userPoolClientId) missing.push("VITE_USER_POOL_CLIENT_ID");
  if (!config.identityPoolId) missing.push("VITE_IDENTITY_POOL_ID");
  return missing;
}
