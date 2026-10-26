import type { AppConfig } from "../config.js";
import { log } from "../logger.js";

/**
 * Obtains and caches a Cognito access token via the OAuth2 client_credentials
 * grant, for use as `Authorization: Bearer <jwt>` against the CUSTOM_JWT gateway.
 *
 * The gateway is configured with a Cognito discovery URL; we read the
 * token_endpoint from that discovery document, then exchange the app client
 * id/secret for an access token.
 */
export interface CognitoTokenManager {
  getToken(): Promise<string>;
}

interface CachedToken {
  accessToken: string;
  expiresAtMs: number;
}

async function resolveTokenEndpoint(discoveryUrl: string): Promise<string> {
  const res = await fetch(discoveryUrl);
  if (!res.ok) {
    throw new Error(
      `Failed to fetch OIDC discovery doc (${res.status}) from ${discoveryUrl}`,
    );
  }
  const doc = (await res.json()) as { token_endpoint?: string };
  if (!doc.token_endpoint) {
    throw new Error(`Discovery doc at ${discoveryUrl} has no token_endpoint`);
  }
  return doc.token_endpoint;
}

async function requestClientCredentialsToken(
  tokenEndpoint: string,
  clientId: string,
  clientSecret: string,
): Promise<{ accessToken: string; expiresInSec: number }> {
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const body = new URLSearchParams({ grant_type: "client_credentials" });
  // Some Cognito resource servers require an explicit `scope`. Start without;
  // if the endpoint 400s asking for one, set COGNITO_SCOPE and it is added here.
  if (process.env.COGNITO_SCOPE) body.set("scope", process.env.COGNITO_SCOPE);

  const res = await fetch(tokenEndpoint, {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `Cognito token request failed (${res.status}): ${text}. ` +
        `If it mentions scope, set COGNITO_SCOPE.`,
    );
  }
  const json = (await res.json()) as {
    access_token: string;
    expires_in: number;
  };
  return { accessToken: json.access_token, expiresInSec: json.expires_in };
}

export function createCognitoTokenManager(cfg: AppConfig): CognitoTokenManager {
  let cached: CachedToken | null = null;
  let tokenEndpoint: string | null = null;
  let inFlight: Promise<string> | null = null;

  async function refresh(): Promise<string> {
    if (!tokenEndpoint) {
      tokenEndpoint = await resolveTokenEndpoint(cfg.discoveryUrl);
      log.info("Resolved Cognito token endpoint", { tokenEndpoint });
    }
    const { accessToken, expiresInSec } = await requestClientCredentialsToken(
      tokenEndpoint,
      cfg.clientId,
      cfg.clientSecret,
    );
    // Refresh at ~90% of the lifetime to avoid using a token about to expire.
    cached = {
      accessToken,
      expiresAtMs: Date.now() + expiresInSec * 1000 * 0.9,
    };
    log.info("Cognito token acquired", { expiresInSec });
    return accessToken;
  }

  return {
    async getToken(): Promise<string> {
      if (cached && Date.now() < cached.expiresAtMs) return cached.accessToken;
      // Collapse concurrent refreshes into one request.
      if (!inFlight) {
        inFlight = refresh().finally(() => {
          inFlight = null;
        });
      }
      return inFlight;
    },
  };
}
