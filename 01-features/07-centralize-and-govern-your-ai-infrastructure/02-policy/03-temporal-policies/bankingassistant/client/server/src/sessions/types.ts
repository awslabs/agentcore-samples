import type { McpClient, McpTool, ProtocolVersion } from "../mcp/types.js";

export interface ToolEvent {
  name: string;
  args: unknown;
  result: unknown;
  isError?: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  toolEvents?: ToolEvent[];
  ts: number;
}

/** Where the policy session id came from: user-provided, gateway-issued, or not set yet. */
export type PolicySessionSource = "user" | "gateway" | null;

export interface Session {
  id: string;
  label: string;
  protocol: ProtocolVersion;
  mcpSessionId: string | null;
  policySessionId: string | null;
  policySessionSource: PolicySessionSource;
  messages: ChatMessage[];
  client: McpClient; // live, server-side only — never serialized
  tools: McpTool[];
}

export interface McpToolInfo {
  name: string;
  description?: string;
}

/** Serializable view sent to the browser (no live client). */
export interface SessionDTO {
  id: string;
  label: string;
  protocol: ProtocolVersion;
  mcpSessionId: string | null;
  policySessionId: string | null;
  policySessionSource: PolicySessionSource;
  messages: ChatMessage[];
  tools: McpToolInfo[];
}

export interface SessionSummary {
  id: string;
  label: string;
  protocol: ProtocolVersion;
  mcpSessionId: string | null;
  policySessionId: string | null;
  policySessionSource: PolicySessionSource;
}

export function toDTO(s: Session): SessionDTO {
  return {
    id: s.id,
    label: s.label,
    protocol: s.protocol,
    mcpSessionId: s.mcpSessionId,
    policySessionId: s.policySessionId,
    policySessionSource: s.policySessionSource,
    messages: s.messages,
    tools: s.tools.map((t) => ({ name: t.name, description: t.description })),
  };
}

export function toSummary(s: Session): SessionSummary {
  return {
    id: s.id,
    label: s.label,
    protocol: s.protocol,
    mcpSessionId: s.mcpSessionId,
    policySessionId: s.policySessionId,
    policySessionSource: s.policySessionSource,
  };
}
