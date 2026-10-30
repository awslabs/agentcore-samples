export type ProtocolVersion = "2025-11-25" | "2026-07-28";

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

export type PolicySessionSource = "user" | "gateway" | null;

export interface SessionSummary {
  id: string;
  label: string;
  protocol: ProtocolVersion;
  mcpSessionId: string | null;
  policySessionId: string | null;
  policySessionSource: PolicySessionSource;
}

export interface McpToolInfo {
  name: string;
  description?: string;
}

export interface SessionDTO extends SessionSummary {
  messages: ChatMessage[];
  tools: McpToolInfo[];
}

export interface AppConfigDTO {
  gatewayUrl: string;
  region: string;
  mock: boolean;
}
