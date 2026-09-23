export type ProtocolVersion = "2025-11-25" | "2026-07-28";

export const POLICY_SESSION_HEADER = "x-amzn-bedrock-agentcore-policy-session-id";

export interface McpTool {
  name: string; // e.g. "banking-tools___get_account_balance"
  description?: string;
  inputSchema: Record<string, unknown>; // JSON Schema, fed to Bedrock Converse
}

export interface ToolCallResult {
  content: unknown; // MCP tool result content (text / json blocks)
  isError?: boolean;
}

/**
 * One abstraction over both MCP protocol revisions (and the mock). The session
 * store owns the policy-session id via getPolicySessionId/onPolicySessionId, so
 * the first request omits the header and every later one reuses the captured id.
 */
export interface McpClient {
  readonly protocol: ProtocolVersion;
  readonly mcpSessionId: string | null; // transport Mcp-Session-Id, or null (stateless)
  readonly policySessionId: string | null; // AgentCore policy session, captured lazily

  connect(): Promise<void>;
  listTools(): Promise<McpTool[]>;
  callTool(name: string, args: Record<string, unknown>): Promise<ToolCallResult>;
  close(): Promise<void>;
}

export interface McpClientDeps {
  mcpUrl: string;
  getToken: () => Promise<string>;
  /** Current policy session id, or null if not captured yet (first request). */
  getPolicySessionId: () => string | null;
  /** Called when the gateway returns a policy session id in a response header. */
  onPolicySessionId: (id: string) => void;
}

/** Raised when the gateway invalidates a session (HTTP 409). */
export class SessionInvalidatedError extends Error {
  constructor(message = "Policy session invalidated. Start a new session.") {
    super(message);
    this.name = "SessionInvalidatedError";
  }
}
