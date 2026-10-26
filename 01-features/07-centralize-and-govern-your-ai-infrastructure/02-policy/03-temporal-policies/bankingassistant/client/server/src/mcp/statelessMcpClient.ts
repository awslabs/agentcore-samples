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
 * MCP client for the 2026-07-28 STATELESS protocol revision, per
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-list.html
 *
 * Stateless means no `initialize` handshake and no Mcp-Session-Id. Each request
 * is a self-contained JSON-RPC POST that carries:
 *   - header  MCP-Protocol-Version: 2026-07-28
 *   - header  Mcp-Method: <the method, e.g. tools/list, tools/call>
 *   - header  Mcp-Name: <the tool name> (required for tools/call)
 *   - params._meta with the protocol version, client info, and capabilities
 *
 * The gateway enforces that these headers match the body, so Mcp-Name must equal
 * params.name on tools/call. Tools are listed with `tools/list` (not
 * server/discover). Everything protocol-specific is confined to this file;
 * DEBUG_MCP=1 logs raw traffic.
 */
const VERSION: ProtocolVersion = "2026-07-28";
const META_VERSION = "io.modelcontextprotocol/protocolVersion";
const META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo";
const META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities";

const CLIENT_INFO = { name: "banking-assistant-web", version: "1.0.0" };


export function createStatelessMcpClient(deps: McpClientDeps): McpClient {
  let rpcId = 0;

  async function rpc(
    method: string,
    params: Record<string, unknown>,
    mcpName?: string,
  ): Promise<any> {
    const token = await deps.getToken();
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      "MCP-Protocol-Version": VERSION,
      "Mcp-Method": method,
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    // On 2026-07-28 the gateway requires an Mcp-Name header naming the tool for
    // tools/call, and rejects the request if it contradicts params.name.
    if (mcpName) headers["Mcp-Name"] = mcpName;
    const policyId = deps.getPolicySessionId();
    if (policyId) {
      headers[POLICY_SESSION_HEADER] = policyId;
    }
    log.info("stateless MCP policy-session state", { method, policyIdSent: policyId ?? "(omitted)" });

    const id = String(++rpcId);
    const body = {
      jsonrpc: "2.0",
      id,
      method,
      params: {
        ...params,
        _meta: {
          [META_VERSION]: VERSION,
          [META_CLIENT_INFO]: CLIENT_INFO,
          [META_CLIENT_CAPS]: {},
        },
      },
    };

    const bodyStr = JSON.stringify(body);
    log.info("stateless MCP request", { method, id, mcpName });
    log.debug("stateless MCP request body", { bodyStr });

    const res = await fetch(deps.mcpUrl, {
      method: "POST",
      headers,
      body: bodyStr,
    });

    const returned = res.headers.get(POLICY_SESSION_HEADER);
    if (returned && returned !== deps.getPolicySessionId()) {
      deps.onPolicySessionId(returned);
    }
    if (res.status === 409) throw new SessionInvalidatedError();
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`MCP ${method} failed (${res.status}): ${text}`);
    }

    // The gateway may reply as plain JSON (closed body) or as an SSE stream
    // (text/event-stream) that stays open for notifications/progress. For SSE,
    // read incrementally and resolve on the first complete JSON-RPC result
    // rather than waiting for the stream to close (it may never close).
    const contentType = res.headers.get("content-type") ?? "";
    log.info("stateless MCP response headers", { method, status: res.status, contentType, hasBody: Boolean(res.body) });
    let json: any;

    if (contentType.includes("text/event-stream") && res.body) {
      json = await readFirstSseResult(res.body);
    } else {
      const text = await res.text();
      log.info("stateless MCP response (json)", { status: res.status, text: text.slice(0, 500) });
      json = JSON.parse(text);
    }

    if (json.error) {
      throw new Error(`MCP ${method} error: ${JSON.stringify(json.error)}`);
    }
    return json.result;
  }

  /** Read an SSE stream until we find the first `data:` line with a JSON-RPC result, then return it. */
  async function readFirstSseResult(body: ReadableStream<Uint8Array>): Promise<any> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        log.info("SSE chunk received", { len: chunk.length, preview: chunk.slice(0, 200) });
        buffer += chunk;

        // Process complete lines
        let nl: number;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);

          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === "[DONE]") continue;

          try {
            const parsed = JSON.parse(payload);
            // A JSON-RPC response has either `result` or `error`
            if ("result" in parsed || "error" in parsed) {
              log.debug("stateless MCP response (sse)", { parsed });
              reader.cancel();
              return parsed;
            }
          } catch {
            // Not valid JSON yet, keep reading
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    throw new Error("SSE stream ended without a JSON-RPC result");
  }

  return {
    protocol: VERSION,
    mcpSessionId: null, // stateless: no transport session
    get policySessionId() {
      return deps.getPolicySessionId();
    },

    async connect(): Promise<void> {
      // No handshake in the stateless protocol.
    },

    async listTools(): Promise<McpTool[]> {
      const result = await rpc("tools/list", {});
      const tools = (result?.tools ?? []) as Array<{
        name: string;
        description?: string;
        inputSchema?: Record<string, unknown>;
      }>;
      return tools.map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema ?? { type: "object", properties: {} },
      }));
    },

    async callTool(
      name: string,
      args: Record<string, unknown>,
    ): Promise<ToolCallResult> {
      const result = await rpc("tools/call", { name, arguments: args }, name);
      return { content: result?.content ?? result, isError: Boolean(result?.isError) };
    },

    async close(): Promise<void> {
      // Nothing to tear down.
    },
  };
}
