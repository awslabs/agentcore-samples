// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Discovery over the registry's **MCP endpoint**, authorized with a Cognito
 * bearer token (the registry's CUSTOM_JWT authorizer).
 *
 * Why not the data-plane SDK client here? Because a CUSTOM_JWT registry authorizes
 * consumers with `Authorization: Bearer <JWT>` rather than SigV4, and the MCP
 * endpoint is the documented, GA surface for exactly that. Using it means this app
 * consumes the registry through the *same* endpoint + token pair that it tells you
 * to paste into Kiro, Amazon Quick or Claude — so the connection instructions on the
 * registry page are proven by the app itself rather than merely documented.
 *
 * Namespace note: this targets the GA `agent-registry` namespace
 * (`https://agent-registry.<region>.api.aws/registry/<registryId>/mcp`), which
 * exposes all three discovery tools. The deprecated `bedrock-agentcore` namespace
 * exposed only a single `search_registry_records` tool and is not used anywhere here.
 *
 * Protocol: MCP spec 2025-11-25 over streamable HTTP. The endpoint is stateless for
 * `tools/call`, so no `initialize` handshake is required — a single JSON-RPC POST
 * per call, which is what the AWS docs' own curl example does.
 */

const MCP_PROTOCOL_VERSION = "2025-11-25";

export type McpToolName =
  | "search_discoverable_registry_records"
  | "list_discoverable_registry_records"
  | "batch_get_discoverable_registry_record";

interface JsonRpcResponse {
  jsonrpc?: string;
  id?: number | string;
  result?: {
    content?: Array<{ type?: string; text?: string }>;
    structuredContent?: unknown;
    isError?: boolean;
  };
  error?: { code?: number; message?: string; data?: unknown };
}

/** An MCP-endpoint failure, shaped so `toFriendlyError` can classify it. */
export class McpError extends Error {
  readonly name = "McpError";
  readonly status?: number;
  /** Set for 401/403 so the UI can render the same guidance as an IAM AccessDenied. */
  readonly isAuthFailure: boolean;

  constructor(message: string, opts: { status?: number } = {}) {
    super(message);
    this.status = opts.status;
    this.isAuthFailure = opts.status === 401 || opts.status === 403;
  }
}

let requestId = 0;

/**
 * Invoke one tool on the registry's MCP endpoint and return its decoded payload.
 *
 * @param endpoint    Full MCP endpoint URL for the registry.
 * @param accessToken Cognito access token — the `client_id` claim is what the
 *                    registry's `allowedClients` list is matched against.
 */
export async function callMcpTool<T>(
  endpoint: string,
  accessToken: string,
  tool: McpToolName,
  args: Record<string, unknown>,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
        // Streamable HTTP servers may answer with either a JSON body or an SSE stream.
        Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: ++requestId,
        method: "tools/call",
        params: { name: tool, arguments: args },
      }),
    });
  } catch (e) {
    // A network-layer rejection here is most often a CORS preflight failure.
    throw new McpError(
      `Could not reach the registry MCP endpoint (${endpoint}). ` +
        `Original error: ${e instanceof Error ? e.message : String(e)}`,
    );
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new McpError(
      `Registry MCP endpoint returned HTTP ${res.status}${body ? `: ${truncate(body)}` : ""}`,
      { status: res.status },
    );
  }

  const payload = parseBody(await res.text());

  if (payload.error) {
    throw new McpError(
      payload.error.message ?? "The registry MCP endpoint returned an error.",
    );
  }
  if (payload.result?.isError) {
    throw new McpError(
      textOf(payload.result.content) || "The MCP tool call failed.",
    );
  }

  // Prefer structured output; fall back to the JSON carried in the text content block.
  if (payload.result?.structuredContent !== undefined) {
    return payload.result.structuredContent as T;
  }
  const text = textOf(payload.result?.content);
  if (!text) return {} as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new McpError(
      `Could not parse the MCP tool result as JSON: ${truncate(text)}`,
    );
  }
}

/**
 * Streamable HTTP may answer a single JSON-RPC object or an SSE stream. For a
 * one-shot `tools/call` the SSE form carries exactly one `data:` event, so taking
 * the last `data:` line covers both shapes.
 */
function parseBody(raw: string): JsonRpcResponse {
  const trimmed = raw.trim();
  if (!trimmed) return {};
  if (!trimmed.startsWith("data:") && !trimmed.includes("\ndata:")) {
    return JSON.parse(trimmed) as JsonRpcResponse;
  }
  const dataLines = trimmed
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).trim());
  const last = dataLines[dataLines.length - 1];
  if (!last)
    throw new McpError("Empty SSE response from the registry MCP endpoint.");
  return JSON.parse(last) as JsonRpcResponse;
}

function textOf(
  content: Array<{ type?: string; text?: string }> | undefined,
): string {
  if (!content) return "";
  return content
    .filter((c) => c.text)
    .map((c) => c.text)
    .join("\n");
}

function truncate(s: string, max = 300): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}
