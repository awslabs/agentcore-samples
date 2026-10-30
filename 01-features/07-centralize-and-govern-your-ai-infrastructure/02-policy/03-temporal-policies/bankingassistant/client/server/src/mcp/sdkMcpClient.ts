import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { log } from "../logger.js";
import {
  McpClient,
  McpClientDeps,
  McpTool,
  POLICY_SESSION_HEADER,
  ProtocolVersion,
  SessionInvalidatedError,
  ToolCallResult,
} from "./types.js";

/**
 * MCP client for the 2025-11-25 handshake protocol, using the official SDK.
 *
 * The SDK transport takes static headers at construction, but we need per-request
 * control: a fresh bearer token, conditional policy-session header (omit on the
 * first request), and access to the response header the gateway returns. We get
 * all three by passing a custom `fetch` to StreamableHTTPClientTransport.
 *
 * The gateway expires the transport-level Mcp-Session-Id after its session
 * timeout (1h). A later call then fails with JSON-RPC code -32004
 * ("Session not found or expired"). We detect that, transparently reconnect
 * (fresh initialize handshake -> new session id), and retry the call once.
 */
export function createSdkMcpClient(deps: McpClientDeps): McpClient {
  const protocol: ProtocolVersion = "2025-11-25";

  const customFetch: typeof fetch = async (input, init) => {
    const token = await deps.getToken();
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${token}`);
    // Force plain JSON responses from the gateway. Without this, the gateway
    // returns text/event-stream (SSE) which keeps the connection open for
    // notifications/progress. The SDK's SSE parser then hangs waiting for
    // more events even after the tool result has been delivered.
    headers.set("Accept", "application/json");

    const policyId = deps.getPolicySessionId();
    if (policyId) headers.set(POLICY_SESSION_HEADER, policyId);
    else headers.delete(POLICY_SESSION_HEADER);

    const res = await fetch(input, { ...init, headers });

    const returned = res.headers.get(POLICY_SESSION_HEADER);
    if (returned && returned !== deps.getPolicySessionId()) {
      deps.onPolicySessionId(returned);
    }
    if (res.status === 409) throw new SessionInvalidatedError();
    return res;
  };

  // Transport and client are rebuilt on reconnect, so keep them mutable.
  let transport = new StreamableHTTPClientTransport(new URL(deps.mcpUrl), {
    fetch: customFetch,
  });
  let client = new Client(
    { name: "banking-assistant-web", version: "1.0.0" },
    { capabilities: {} },
  );

  async function connect(): Promise<void> {
    await client.connect(transport);
    log.info("SDK MCP client connected", { mcpSessionId: transport.sessionId });
  }

  async function reconnect(): Promise<void> {
    try {
      await client.close();
    } catch {
      // Old transport may already be dead; ignore.
    }
    transport = new StreamableHTTPClientTransport(new URL(deps.mcpUrl), {
      fetch: customFetch,
    });
    client = new Client(
      { name: "banking-assistant-web", version: "1.0.0" },
      { capabilities: {} },
    );
    await connect();
    log.info("SDK MCP client reconnected after session expiry");
  }

  // The gateway signals an expired transport session with code -32004 or a
  // "session not found or expired" message. Reconnecting mints a new session id.
  function isSessionExpired(err: unknown): boolean {
    const msg = err instanceof Error ? err.message : String(err);
    return /-32004/.test(msg) || /session not found or expired/i.test(msg);
  }

  // Run an operation; if it fails because the transport session expired,
  // reconnect once and retry.
  async function withReconnect<T>(op: () => Promise<T>): Promise<T> {
    try {
      return await op();
    } catch (err) {
      if (!isSessionExpired(err)) throw err;
      log.warn("MCP transport session expired; reconnecting and retrying");
      await reconnect();
      return op();
    }
  }

  return {
    protocol,
    get mcpSessionId() {
      return transport.sessionId ?? null;
    },
    get policySessionId() {
      return deps.getPolicySessionId();
    },

    connect,

    async listTools(): Promise<McpTool[]> {
      const res = await withReconnect(() => client.listTools());
      return res.tools.map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: (t.inputSchema as Record<string, unknown>) ?? {
          type: "object",
          properties: {},
        },
      }));
    },

    async callTool(
      name: string,
      args: Record<string, unknown>,
    ): Promise<ToolCallResult> {
      const res = await withReconnect(() => client.callTool({ name, arguments: args }));
      return { content: res.content, isError: Boolean(res.isError) };
    },

    async close(): Promise<void> {
      await client.close();
    },
  };
}
