import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Application configuration, assembled from the deployed setup_config.json
 * (written by setup.py) plus environment variables (README Step 1).
 */
export interface AppConfig {
  gatewayUrl: string; // base gateway URL, trailing slash stripped
  mcpUrl: string; // `${gatewayUrl}/mcp`
  region: string;
  discoveryUrl: string;
  clientId: string;
  clientSecret: string;
  bedrockModelId: string;
  port: number;
  mockMcp: boolean;
  mockLlm: boolean;
}

const DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0";

// setup_config.json lives at the bankingassistant/ root, two levels above
// this file's package (client/server/src -> client/server -> client -> root).
function findSetupConfigPath(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  // src/ -> server/ -> client/ -> bankingassistant/
  return resolve(here, "..", "..", "..", "setup_config.json");
}

// Normalize to the base gateway URL: strip trailing slashes and a trailing
// `/mcp` if present, so callers can append `/mcp` exactly once.
function normalizeGatewayUrl(url: string): string {
  return url.replace(/\/+$/, "").replace(/\/mcp$/, "");
}

function readGatewayUrl(): string {
  // An explicit env var always wins (handy for MOCK_MCP without a deploy).
  if (process.env.GATEWAY_URL) return normalizeGatewayUrl(process.env.GATEWAY_URL);

  const path = findSetupConfigPath();
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    throw new Error(
      `Could not read ${path}. Run \`uv run setup.py\` first, or set GATEWAY_URL.`,
    );
  }
  const cfg = JSON.parse(raw) as { gateway_url?: string };
  if (!cfg.gateway_url) {
    throw new Error(`setup_config.json has no "gateway_url". Re-run setup.py.`);
  }
  return normalizeGatewayUrl(cfg.gateway_url);
}

export function loadConfig(): AppConfig {
  const mockMcp = Boolean(process.env.MOCK_MCP);

  const region = process.env.REGION || "us-east-1";
  const discoveryUrl = process.env.DISCOVERY_URL || "";
  const clientId = process.env.GATEWAY_CLIENT_ID || "";
  const clientSecret = process.env.GATEWAY_CLIENT_SECRET || "";

  // In mock mode we do not need Cognito or a real gateway.
  const gatewayUrl = mockMcp
    ? normalizeGatewayUrl(process.env.GATEWAY_URL || "https://mock.local")
    : readGatewayUrl();

  if (!mockMcp) {
    const missing = Object.entries({
      DISCOVERY_URL: discoveryUrl,
      GATEWAY_CLIENT_ID: clientId,
      GATEWAY_CLIENT_SECRET: clientSecret,
    })
      .filter(([, v]) => !v)
      .map(([k]) => k);
    if (missing.length) {
      throw new Error(
        `Missing required environment variables: ${missing.join(", ")}. ` +
          `See client/.env.example and README Step 1. ` +
          `(Or set MOCK_MCP=1 to run without a live gateway.)`,
      );
    }
  }

  return {
    gatewayUrl,
    mcpUrl: `${gatewayUrl}/mcp`,
    region,
    discoveryUrl,
    clientId,
    clientSecret,
    bedrockModelId: process.env.BEDROCK_MODEL_ID || DEFAULT_MODEL,
    port: Number(process.env.PORT || 8787),
    mockMcp,
    mockLlm: Boolean(process.env.MOCK_LLM),
  };
}
