import {
  McpClient,
  McpClientDeps,
  McpTool,
  ProtocolVersion,
  ToolCallResult,
} from "./types.js";

/**
 * Offline fake, enabled with MOCK_MCP=1. Returns the real banking/portfolio tool
 * shapes and canned results so the whole UI, session switching, and Converse loop
 * can be exercised with no gateway. Fabricates a policy session id on first call.
 */
const MOCK_TOOLS: McpTool[] = [
  {
    name: "banking-tools___get_account_balance",
    description: "Look up an account balance.",
    inputSchema: {
      type: "object",
      properties: { account_id: { type: "string" } },
      required: ["account_id"],
    },
  },
  {
    name: "banking-tools___transfer_funds",
    description: "Transfer funds between accounts.",
    inputSchema: {
      type: "object",
      properties: {
        from_account: { type: "string" },
        to_account: { type: "string" },
        amount: { type: "number" },
        memo: { type: "string" },
      },
      required: ["from_account", "to_account", "amount"],
    },
  },
  {
    name: "portfolio-tools___get_client_profile",
    description: "Retrieve a client's profile and portfolio IDs.",
    inputSchema: {
      type: "object",
      properties: { client_id: { type: "string" } },
      required: ["client_id"],
    },
  },
];

export function createMockMcpClient(deps: McpClientDeps, protocol: ProtocolVersion): McpClient {
  return {
    protocol,
    mcpSessionId: protocol === "2025-11-25" ? `mock-mcp-${Date.now()}` : null,
    get policySessionId() {
      return deps.getPolicySessionId();
    },

    async connect(): Promise<void> {},

    async listTools(): Promise<McpTool[]> {
      return MOCK_TOOLS;
    },

    async callTool(
      name: string,
      args: Record<string, unknown>,
    ): Promise<ToolCallResult> {
      // Fabricate the gateway-issued policy id on the first tool call.
      if (!deps.getPolicySessionId()) {
        deps.onPolicySessionId(`mock-policy-${Math.abs(hash(name))}`);
      }
      const text =
        name.endsWith("get_account_balance")
          ? JSON.stringify({ accountId: args.account_id, balance: 85000, currency: "USD" })
          : name.endsWith("get_client_profile")
            ? JSON.stringify({ clientId: args.client_id, portfolioIds: ["PORT-8821"] })
            : JSON.stringify({ ok: true, tool: name, args });
      return { content: [{ type: "text", text }], isError: false };
    },

    async close(): Promise<void> {},
  };
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i);
  return h;
}
